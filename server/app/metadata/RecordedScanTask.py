from __future__ import annotations

import anyio
import asyncio
import concurrent.futures
import pathlib
from datetime import datetime
from tortoise import transactions
from typing import ClassVar
from watchfiles import awatch, Change
from zoneinfo import ZoneInfo

from app import logging
from app import schemas
from app.config import Config
from app.metadata.MetadataAnalyzer import MetadataAnalyzer
from app.metadata.KeyFrameAnalyzer import KeyFrameAnalyzer
from app.models.Channel import Channel
from app.models.RecordedProgram import RecordedProgram
from app.models.RecordedVideo import RecordedVideo


class RecordedScanTask:
    """
    録画フォルダの監視とメタデータの DB への同期を行うタスク
    サーバーの起動中は常時稼働し続け、以下の処理を担う
    - サーバー起動時の録画フォルダの一括スキャン・同期
    - 録画フォルダ以下のファイルシステム変更の監視を開始し、変更があれば随時メタデータを解析後、DB に永続化
    - 録画中ファイルの状態管理
    """

    # スキャン対象の拡張子
    SCAN_TARGET_EXTENSIONS: ClassVar[list[str]] = ['.ts', '.m2t', '.m2ts', '.mts']

    # 録画中ファイルの更新イベントを間引く間隔 (秒)
    UPDATE_THROTTLE_SECONDS: ClassVar[int] = 30

    # 録画完了と判断するまでの無更新時間 (秒)
    RECORDING_COMPLETE_SECONDS: ClassVar[int] = 30

    # 録画中と判断する最大の経過時間 (秒)
    RECORDING_MAX_AGE_SECONDS: ClassVar[int] = 300  # 5分

    # 録画中ファイルの最小データ長 (秒)
    MINIMUM_RECORDING_SECONDS: ClassVar[int] = 60


    def __init__(self) -> None:
        """
        録画フォルダの監視タスクを初期化する
        """

        # 設定を読み込む
        self.config = Config()
        self.recorded_folders = [anyio.Path(folder) for folder in self.config.video.recorded_folders]

        # 録画中ファイルの状態管理
        ## path: (last_modified, last_checked, file_size)
        self._recording_files: dict[anyio.Path, tuple[datetime, datetime, int]] = {}

        # タスクの状態管理
        self._is_running = False
        self._task: asyncio.Task[None] | None = None

        # バックグラウンドタスクの状態管理
        self._background_tasks: dict[anyio.Path, asyncio.Task[None]] = {}


    async def start(self) -> None:
        """
        録画フォルダの監視タスクを開始する
        このメソッドはサーバー起動時に app.py から自動的に呼ばれ、サーバーの起動中は常時稼働し続ける
        """

        # 既に実行中の場合は何もしない
        if self._is_running:
            return
        self._is_running = True

        # バックグラウンドタスクとして実行
        self._task = asyncio.create_task(self.run())


    async def stop(self) -> None:
        """
        録画フォルダの監視タスクを停止する
        このメソッドはサーバー終了時に app.py から自動的に呼ばれる
        """

        # 既に停止中の場合は何もしない
        if not self._is_running:
            return

        # 実行中タスクを停止
        self._is_running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


    async def run(self) -> None:
        """
        録画フォルダ以下の一括スキャンと DB への同期を実行した後、
        録画フォルダ以下のファイルシステム変更の監視を開始し、変更があれば随時メタデータを解析後、DB に永続化する
        このメソッドは start() 経由でサーバー起動時に app.py から自動的に呼ばれ、サーバーの起動中は常時稼働し続ける
        """

        try:
            # サーバー起動時の一括スキャン・同期を実行
            await self.runBatchScan()
            # 録画フォルダの監視を開始
            await self.watchRecordedFolders()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logging.error('Error in RecordedScanTask:', exc_info=ex)
        finally:
            self._is_running = False


    async def runBatchScan(self) -> None:
        """
        録画フォルダ以下の一括スキャンと DB への同期を実行する
        - 録画フォルダ内の全 TS ファイルをスキャン
        - 追加・変更があったファイルのみメタデータを解析し、DB に永続化
        - 存在しない録画ファイルに対応するレコードを一括削除
        """

        logging.info('Batch scan of recording folders has been started.')

        # 現在登録されている全ての RecordedVideo レコードをキャッシュ
        existing_db_recorded_videos = {
            anyio.Path(video.file_path): video for video in await RecordedVideo.all()
                .select_related('recorded_program', 'recorded_program__channel')
        }

        # 各録画フォルダをスキャン
        for folder in self.recorded_folders:
            async for file_path in folder.rglob('*'):
                try:
                    # Mac の metadata ファイルをスキップ
                    if file_path.name.startswith('._'):
                        continue
                    # TS ファイル以外をスキップ
                    if file_path.suffix.lower() not in self.SCAN_TARGET_EXTENSIONS:
                        continue
                    # 録画ファイルが確実に存在することを確認する
                    ## 環境次第では、稀に glob で取得したファイルが既に存在しなくなっているケースがある
                    if not await file_path.is_file():
                        continue

                    # 見つかったファイルを処理
                    await self.__processRecordedFile(file_path, existing_db_recorded_videos)
                except Exception as ex:
                    logging.error(f'{file_path}: Failed to process recorded file:', exc_info=ex)

        # 存在しない録画ファイルに対応するレコードを一括削除
        ## トランザクション配下に入れることでパフォーマンスが向上する
        async with transactions.in_transaction():
            for file_path, existing_db_recorded_video in existing_db_recorded_videos.items():
                # ファイルの存在確認を非同期に行う
                if not await file_path.is_file():
                    # RecordedVideo の親テーブルである RecordedProgram を削除すると、
                    # CASCADE 制約により RecordedVideo も同時に削除される (Channel は親テーブルにあたるため削除されない)
                    await existing_db_recorded_video.recorded_program.delete()
                    logging.info(f'{file_path}: Deleted record for non-existent file.')

        logging.info('Batch scan of recording folders has been completed.')


    async def __processRecordedFile(
        self,
        file_path: anyio.Path,
        existing_db_recorded_videos: dict[anyio.Path, RecordedVideo] | None,
    ) -> None:
        """
        指定された録画ファイルのメタデータを解析し、DB に永続化する
        既に当該ファイルの情報が DB に登録されており、ファイル内容に変更がない場合は何も行われない

        Args:
            file_path (anyio.Path): 処理対象のファイルパス
            existing_db_recorded_videos (dict[anyio.Path, RecordedVideo] | None): 既に DB に永続化されている録画ファイルパスと RecordedVideo レコードのマッピング
                (ファイル変更イベントから呼ばれた場合、watchfiles 初期化時に取得した全レコードと今で状態が一致しているとは限らないため、None が入る)
        """

        try:
            # 万が一この時点でファイルが存在しない場合はスキップ
            if not await file_path.is_file():
                return

            # ファイルの状態をチェック
            stat = await file_path.stat()
            last_modified = datetime.fromtimestamp(stat.st_mtime, tz=ZoneInfo('Asia/Tokyo'))
            now = datetime.now(tz=ZoneInfo('Asia/Tokyo'))
            file_size = stat.st_size

            # 同じファイルパスの既存レコードがあれば取り出す
            if existing_db_recorded_videos is not None:
                existing_db_recorded_video = existing_db_recorded_videos.pop(file_path, None)
            else:
                existing_db_recorded_video = None

            # この時点で existing_db_recorded_video が None の場合、DB に同一ファイルパスのレコードがないか問い合わせる
            ## ファイル変更イベントから呼ばれた場合は existing_db_recorded_videos が None になるが、
            ## DB には同一ファイルパスのレコードが存在する可能性がある
            if existing_db_recorded_video is None:
                existing_db_recorded_video = await RecordedVideo.get_or_none(
                    file_path=str(file_path)
                ).select_related('recorded_program', 'recorded_program__channel')

            # 現在録画中とマークされているファイルの処理
            is_recording = file_path in self._recording_files
            if is_recording:
                # 既に DB に登録済みで録画中の場合は再解析しない
                if existing_db_recorded_video is not None and existing_db_recorded_video.status == 'Recording':
                    return
                # ファイルサイズが前回と変わっていない場合はスキップ
                last_size = self._recording_files[file_path][2]
                if file_size == last_size:
                    return

            # 現在のファイルハッシュを計算
            try:
                analyzer = MetadataAnalyzer(pathlib.Path(str(file_path)))  # anyio.Path -> pathlib.Path に変換
                file_hash = analyzer.calculateTSFileHash()
            except ValueError:
                # ファイルサイズが小さすぎる場合はスキップ
                logging.warning(f'{file_path}: File size is too small. ignored.')
                return

            # 同じファイルパスの既存レコードがある場合、先ほど計算した最新のハッシュと変わっていない場合は
            # レコードの内容を更新する必要がないのでスキップ
            if existing_db_recorded_video is not None and existing_db_recorded_video.file_hash == file_hash:
                return

            # ProcessPoolExecutor を使い、別プロセス上でメタデータを解析
            ## メタデータ解析処理は実装上同期 I/O で実装されており、また CPU-bound な処理のため、別プロセスで実行している
            ## with 文で括ることで、with 文を抜けたときに ProcessPoolExecutor がクリーンアップされるようにする
            ## さもなければサーバーの終了後もプロセスが残り続けてゾンビプロセス化し、メモリリークを引き起こしてしまう
            loop = asyncio.get_running_loop()
            with concurrent.futures.ProcessPoolExecutor() as executor:
                recorded_program = await loop.run_in_executor(executor, analyzer.analyze)

            if recorded_program is None:
                # メタデータ解析に失敗した場合はエラーとして扱う
                logging.error(f'{file_path}: Failed to analyze metadata.')
                return

            # 60秒未満のファイルは録画失敗または切り抜きとみなしてスキップ
            # 録画中だがまだ60秒に満たない場合、今後のファイル変更イベント発火時に60秒を超えていれば録画中ファイルとして処理される
            if recorded_program.recorded_video.duration < self.MINIMUM_RECORDING_SECONDS:
                logging.debug_simple(f'{file_path}: This file is too short (duration < {self.MINIMUM_RECORDING_SECONDS}s). Skipped.')
                return

            # 録画中のファイルとして処理
            ## 他ドライブからファイルコピー中のファイルも、実際の録画処理より高速に書き込まれるだけで随時書き込まれることに変わりはないので、
            ## 録画中として判断されることがある（その場合、ファイルコピーが完了した段階で「録画完了」扱いとなる）
            if is_recording or (now - last_modified).total_seconds() < self.RECORDING_COMPLETE_SECONDS:
                # status を Recording に設定
                recorded_program.recorded_video.status = 'Recording'
                # 状態を更新
                self._recording_files[file_path] = (last_modified, now, file_size)
                logging.debug_simple(f'{file_path}: This file is recording or copying (duration >= {self.MINIMUM_RECORDING_SECONDS}s).')
            else:
                # 録画完了後のバックグラウンド解析タスクを開始
                if file_path not in self._background_tasks:
                    task = asyncio.create_task(self.__runBackgroundAnalysis(file_path))
                    self._background_tasks[file_path] = task
                # status を Recorded に設定
                # MetadataAnalyzer 側で既に Recorded に設定されているが、念のため
                recorded_program.recorded_video.status = 'Recorded'

            # DB に永続化
            await self.__saveRecordedMetadataToDB(recorded_program, existing_db_recorded_video)
            logging.info(f'{file_path}: {"Updated" if existing_db_recorded_video else "Saved"} metadata to DB. (status: {recorded_program.recorded_video.status})')

        except Exception as ex:
            logging.error(f'{file_path}: Error processing file:', exc_info=ex)
            raise


    @staticmethod
    async def __saveRecordedMetadataToDB(
        recorded_program: schemas.RecordedProgram,
        existing_db_recorded_video: RecordedVideo | None,
    ) -> None:
        """
        録画ファイルのメタデータ解析結果を DB に保存する
        既存レコードがある場合は更新し、ない場合は新規作成する

        Args:
            recorded_program (schemas.RecordedProgram): 保存する録画番組情報
            existing_db_recorded_video (RecordedVideo | None): 既に DB に永続化されている録画ファイルの RecordedVideo レコード
        """

        # トランザクション配下に入れることでパフォーマンスが向上する
        async with transactions.in_transaction():

            # Channel の保存（まだ当該チャンネルが DB に存在しない場合のみ）
            db_channel = None
            if recorded_program.channel is not None:
                db_channel = await Channel.get_or_none(id=recorded_program.channel.id)
                if db_channel is None:
                    db_channel = Channel()
                    db_channel.id = recorded_program.channel.id
                    db_channel.display_channel_id = recorded_program.channel.display_channel_id
                    db_channel.network_id = recorded_program.channel.network_id
                    db_channel.service_id = recorded_program.channel.service_id
                    db_channel.transport_stream_id = recorded_program.channel.transport_stream_id
                    db_channel.remocon_id = recorded_program.channel.remocon_id
                    db_channel.channel_number = recorded_program.channel.channel_number
                    db_channel.type = recorded_program.channel.type
                    db_channel.name = recorded_program.channel.name
                    db_channel.jikkyo_force = recorded_program.channel.jikkyo_force
                    db_channel.is_subchannel = recorded_program.channel.is_subchannel
                    db_channel.is_radiochannel = recorded_program.channel.is_radiochannel
                    db_channel.is_watchable = recorded_program.channel.is_watchable
                    await db_channel.save()

            # RecordedProgram の保存または更新
            if existing_db_recorded_video is not None:
                db_recorded_program = existing_db_recorded_video.recorded_program
            else:
                db_recorded_program = RecordedProgram()

            # RecordedProgram の属性を設定 (id, created_at, updated_at は自動生成のため指定しない)
            db_recorded_program.recording_start_margin = recorded_program.recording_start_margin
            db_recorded_program.recording_end_margin = recorded_program.recording_end_margin
            db_recorded_program.is_partially_recorded = recorded_program.is_partially_recorded
            db_recorded_program.channel = db_channel  # type: ignore
            db_recorded_program.network_id = recorded_program.network_id
            db_recorded_program.service_id = recorded_program.service_id
            db_recorded_program.event_id = recorded_program.event_id
            db_recorded_program.series_id = recorded_program.series_id
            db_recorded_program.series_broadcast_period_id = recorded_program.series_broadcast_period_id
            db_recorded_program.title = recorded_program.title
            db_recorded_program.series_title = recorded_program.series_title
            db_recorded_program.episode_number = recorded_program.episode_number
            db_recorded_program.subtitle = recorded_program.subtitle
            db_recorded_program.description = recorded_program.description
            db_recorded_program.detail = recorded_program.detail
            db_recorded_program.start_time = recorded_program.start_time
            db_recorded_program.end_time = recorded_program.end_time
            db_recorded_program.duration = recorded_program.duration
            db_recorded_program.is_free = recorded_program.is_free
            db_recorded_program.genres = recorded_program.genres
            db_recorded_program.primary_audio_type = recorded_program.primary_audio_type
            db_recorded_program.primary_audio_language = recorded_program.primary_audio_language
            db_recorded_program.secondary_audio_type = recorded_program.secondary_audio_type
            db_recorded_program.secondary_audio_language = recorded_program.secondary_audio_language
            await db_recorded_program.save()

            # RecordedVideo の保存または更新
            if existing_db_recorded_video is not None:
                db_recorded_video = existing_db_recorded_video
            else:
                db_recorded_video = RecordedVideo()

            # RecordedVideo の属性を設定 (id, created_at, updated_at は自動生成のため指定しない)
            db_recorded_video.recorded_program = db_recorded_program
            db_recorded_video.status = recorded_program.recorded_video.status
            db_recorded_video.file_path = str(recorded_program.recorded_video.file_path)
            db_recorded_video.file_hash = recorded_program.recorded_video.file_hash
            db_recorded_video.file_size = recorded_program.recorded_video.file_size
            db_recorded_video.file_created_at = recorded_program.recorded_video.file_created_at
            db_recorded_video.file_modified_at = recorded_program.recorded_video.file_modified_at
            db_recorded_video.recording_start_time = recorded_program.recorded_video.recording_start_time
            db_recorded_video.recording_end_time = recorded_program.recorded_video.recording_end_time
            db_recorded_video.duration = recorded_program.recorded_video.duration
            db_recorded_video.container_format = recorded_program.recorded_video.container_format
            db_recorded_video.video_codec = recorded_program.recorded_video.video_codec
            db_recorded_video.video_codec_profile = recorded_program.recorded_video.video_codec_profile
            db_recorded_video.video_scan_type = recorded_program.recorded_video.video_scan_type
            db_recorded_video.video_frame_rate = recorded_program.recorded_video.video_frame_rate
            db_recorded_video.video_resolution_width = recorded_program.recorded_video.video_resolution_width
            db_recorded_video.video_resolution_height = recorded_program.recorded_video.video_resolution_height
            db_recorded_video.primary_audio_codec = recorded_program.recorded_video.primary_audio_codec
            db_recorded_video.primary_audio_channel = recorded_program.recorded_video.primary_audio_channel
            db_recorded_video.primary_audio_sampling_rate = recorded_program.recorded_video.primary_audio_sampling_rate
            db_recorded_video.secondary_audio_codec = recorded_program.recorded_video.secondary_audio_codec
            db_recorded_video.secondary_audio_channel = recorded_program.recorded_video.secondary_audio_channel
            db_recorded_video.secondary_audio_sampling_rate = recorded_program.recorded_video.secondary_audio_sampling_rate
            db_recorded_video.key_frames = recorded_program.recorded_video.key_frames
            db_recorded_video.cm_sections = recorded_program.recorded_video.cm_sections
            await db_recorded_video.save()


    async def __runBackgroundAnalysis(self, file_path: anyio.Path) -> None:
        """
        録画完了後のバックグラウンド解析タスク
        - キーフレーム解析
        - CM 区間解析
        など、時間のかかる処理を非同期に実行する

        Args:
            file_path (anyio.Path): 解析対象のファイルパス
        """

        try:
            # 各種解析タスクを非同期に実行
            await asyncio.gather(
                # 録画ファイルのキーフレーム解析
                KeyFrameAnalyzer(file_path).analyze(),
                # TODO: 今後 CM 区間解析などが追加された場合は、ここに追加する
            )
            logging.info(f'{file_path}: Background analysis completed.')

        except Exception as ex:
            logging.error(f'{file_path}: Error in background analysis:', exc_info=ex)
        finally:
            # 完了したタスクを管理対象から削除
            self._background_tasks.pop(file_path, None)


    async def watchRecordedFolders(self) -> None:
        """
        録画フォルダ以下のファイルシステム変更の監視を開始し、変更があれば随時メタデータを解析後、DB に永続化する
        """

        logging.info('Starting file system watch of recording folders.')

        # 監視対象のディレクトリを設定
        watch_paths = [str(path) for path in self.recorded_folders]

        # 録画完了チェック用のタスク
        completion_check_task = asyncio.create_task(self.__checkRecordingCompletion())

        try:
            # watchfiles によるファイル監視
            async for changes in awatch(*watch_paths, recursive=True):
                if not self._is_running:
                    break

                # 変更があったファイルごとに処理
                for change_type, file_path_str in changes:
                    if not self._is_running:
                        break

                    file_path = anyio.Path(file_path_str)
                    # Mac の metadata ファイルをスキップ
                    if file_path.name.startswith('._'):
                        continue
                    # TS ファイル以外は無視
                    if file_path.suffix.lower() not in self.SCAN_TARGET_EXTENSIONS:
                        continue

                    try:
                        # 追加 or 変更イベント
                        if change_type == Change.added or change_type == Change.modified:
                            await self.__handleFileChange(file_path)
                        # 削除イベント
                        elif change_type == Change.deleted:
                            await self.__handleFileDeletion(file_path)
                    except Exception as ex:
                        logging.error(f'{file_path}: Error handling file change:', exc_info=ex)

        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logging.error('Error in file system watch of recording folders:', exc_info=ex)
        finally:
            completion_check_task.cancel()
            try:
                await completion_check_task
            except asyncio.CancelledError:
                pass
            logging.info('File system watch of recording folders has been stopped.')


    async def __handleFileChange(self, file_path: anyio.Path) -> None:
        """
        ファイル追加・変更イベントを受け取り、適切な頻度で __processFile() を呼び出す
        - 録画中ファイルの状態管理
        - メタデータ解析のスロットリング

        Args:
            path (anyio.Path): 追加・変更があったファイルのパス
        """

        try:
            # ファイルの状態をチェック
            stat = await file_path.stat()
            last_modified = datetime.fromtimestamp(stat.st_mtime, tz=ZoneInfo('Asia/Tokyo'))
            now = datetime.now(tz=ZoneInfo('Asia/Tokyo'))
            file_size = stat.st_size

            # 既に録画中とマークされているファイルの処理
            if file_path in self._recording_files:
                last_checked = self._recording_files[file_path][1]
                # 状態を更新
                self._recording_files[file_path] = (last_modified, now, file_size)
                # 前回のチェックから30秒以上経過していない場合はスキップ
                if (now - last_checked).total_seconds() < self.UPDATE_THROTTLE_SECONDS:
                    return
                # メタデータ解析を実行
                await self.__processRecordedFile(file_path, None)
                return

            # 最終更新時刻から一定時間以上経過している場合は録画中とみなさない
            # それ以外の場合、今後継続的に追記されていく（＝録画中）可能性もあるので、録画中マークをつけておく
            if (now - last_modified).total_seconds() <= self.RECORDING_MAX_AGE_SECONDS:
                self._recording_files[file_path] = (last_modified, now, file_size)
            await self.__processRecordedFile(file_path, None)

        except FileNotFoundError:
            # ファイルが既に削除されている場合
            pass
        except Exception as ex:
            logging.error(f'{file_path}: Error handling file change:', exc_info=ex)


    async def __handleFileDeletion(self, file_path: anyio.Path) -> None:
        """
        ファイル削除イベントを受け取り、DB からレコードを削除する

        Args:
            file_path (anyio.Path): 削除されたファイルのパス
        """

        try:
            # 録画中とマークされていたファイルの場合は記録から削除
            self._recording_files.pop(file_path, None)

            # DB からレコードを削除
            db_recorded_video = await RecordedVideo.get_or_none(file_path=str(file_path))
            if db_recorded_video is not None:
                # RecordedVideo の親テーブルである RecordedProgram を削除すると、
                # CASCADE 制約により RecordedVideo も同時に削除される (Channel は親テーブルにあたるため削除されない)
                await db_recorded_video.recorded_program.delete()
                logging.info(f'{file_path}: Deleted record for removed file.')

        except Exception as ex:
            logging.error(f'{file_path}: Error handling file deletion:', exc_info=ex)


    async def __checkRecordingCompletion(self) -> None:
        """
        録画 (またはファイルコピー) の完了状態を定期的にチェックする
        - 30秒間ファイルの更新がない場合に録画完了 (またはファイルコピー完了) と判断
        - 完了したファイルは再度メタデータを解析して DB に保存
        """

        while self._is_running:
            try:
                now = datetime.now(tz=ZoneInfo('Asia/Tokyo'))
                completed_files: list[anyio.Path] = []

                # 録画中ファイルをチェック
                for file_path, (_, _, last_size) in self._recording_files.items():
                    try:
                        # ファイルの現在の状態を取得
                        stat = await file_path.stat()
                        current_modified = datetime.fromtimestamp(stat.st_mtime, tz=ZoneInfo('Asia/Tokyo'))
                        current_size = stat.st_size

                        # 30秒以上更新がなく、かつファイルサイズが変化していない場合は録画完了と判断
                        if ((now - current_modified).total_seconds() >= self.RECORDING_COMPLETE_SECONDS and
                            current_size == last_size):
                            completed_files.append(file_path)
                    except FileNotFoundError:
                        # ファイルが削除された場合は記録から削除
                        completed_files.append(file_path)
                    except Exception as ex:
                        logging.error(f'{file_path}: Error checking recording completion:', exc_info=ex)

                # 完了したファイルを処理
                for file_path in completed_files:
                    try:
                        # 記録から削除
                        self._recording_files.pop(file_path, None)

                        # ファイルが存在する場合のみ再解析
                        if await file_path.is_file():
                            # この時点で、録画（またはファイルコピー）が確実に完了しているはず
                            logging.info(f'{file_path}: Recording or copying has just completed or has already completed.')
                            await self.__processRecordedFile(file_path, None)
                    except Exception as ex:
                        logging.error(f'{file_path}: Error processing completed file:', exc_info=ex)

            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logging.error('Error in recording completion check:', exc_info=ex)

            # 5秒待機
            await asyncio.sleep(5)
