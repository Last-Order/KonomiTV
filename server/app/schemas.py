
# Type Hints を指定できるように
# ref: https://stackoverflow.com/a/33533514/17124142
from __future__ import annotations

import warnings
from datetime import date
from datetime import datetime
from pydantic import BaseModel
from pydantic import RootModel
from typing import Literal, Union
from typing_extensions import TypedDict

# Tortoise ORM がまだ Pydantic V2 に移行できていないため、インポート時や Pydantic モデル定義時に
# 非推奨 API が利用されていることを示す UserWarning が出力される
# 毎回警告が出るのは邪魔なため、このモジュールの読み込みが終わるまで一時的に UserWarning を抑制する
warnings.filterwarnings('ignore', category=UserWarning)
from tortoise.contrib.pydantic import PydanticModel


# モデルとモデルに関連する API レスポンスの構造を表す Pydantic モデル
## この Pydantic モデルに含まれていないカラムは、API レスポンス返却時に自動で除外される (パスワードなど)
## 以前は pydantic_model_creator() で自動生成していたが、だんだん実態と合わなくなってきたため手動で定義している
## PydanticModel を使うところがポイント (BaseModel だとバリデーションエラーが発生する)

# ***** チャンネル *****

class Channel(PydanticModel):
    id: str
    display_channel_id: str
    network_id: int
    service_id: int
    transport_stream_id: int | None
    remocon_id: int
    channel_number: str
    type: str
    name: str
    jikkyo_force: int | None
    is_subchannel: bool
    is_radiochannel: bool
    is_watchable: bool

class LiveChannel(Channel):
    # 以下はすべて動的に生成される TV ライブストリーミング用の追加カラム
    is_display: bool
    viewer_count: int
    program_present: Program | None
    program_following: Program | None

class LiveChannels(BaseModel):
    GR: list[LiveChannel]
    BS: list[LiveChannel]
    CS: list[LiveChannel]
    CATV: list[LiveChannel]
    SKY: list[LiveChannel]
    STARDIGIO: list[LiveChannel]

# ***** 放送中/放送予定の番組 *****

class Program(PydanticModel):
    id: str
    channel_id: str
    network_id: int
    service_id: int
    event_id: int
    title: str
    description: str
    detail: dict[str, str]
    start_time: datetime
    end_time: datetime
    duration: float
    is_free: bool
    genres: list[Genre]
    video_type: str | None
    video_codec: str | None
    video_resolution: str | None
    primary_audio_type: str
    primary_audio_language: str
    primary_audio_sampling_rate: str
    secondary_audio_type: str | None
    secondary_audio_language: str | None
    secondary_audio_sampling_rate: str | None

class Genre(TypedDict):
    major: str
    middle: str

# ***** 録画ファイル *****

class RecordedVideo(PydanticModel):
    id: int
    file_path: str
    file_hash: str
    file_size: int
    recording_start_time: datetime | None
    recording_end_time: datetime | None
    duration: float
    container_format: Literal['MPEG-TS']
    video_codec: Literal['MPEG-2', 'H.264', 'H.265']
    video_codec_profile: Literal['High', 'High 10', 'Main', 'Main 10', 'Baseline']
    video_scan_type: Literal['Interlaced', 'Progressive']
    video_frame_rate: float
    video_resolution_width: int
    video_resolution_height: int
    primary_audio_codec: Literal['AAC-LC', 'HE-AAC', 'MP2']
    primary_audio_channel: Literal['Monaural', 'Stereo', '5.1ch']
    primary_audio_sampling_rate: int
    secondary_audio_codec: Literal['AAC-LC', 'HE-AAC', 'MP2'] | None
    secondary_audio_channel: Literal['Monaural', 'Stereo', '5.1ch'] | None
    secondary_audio_sampling_rate: int | None
    cm_sections: list[CMSection]

class CMSection(TypedDict):
    start_time: float
    end_time: float

# ***** 録画番組 *****

class RecordedProgram(PydanticModel):
    id: int
    recorded_video: RecordedVideo
    recording_start_margin: float
    recording_end_margin: float
    is_partially_recorded: bool
    channel: Channel | None
    network_id: int | None
    service_id: int | None
    event_id: int | None
    series_id: int | None
    series_broadcast_period_id: int | None
    title: str
    series_title: str | None
    episode_number: str | None
    subtitle: str | None
    description: str
    detail: dict[str, str]
    start_time: datetime
    end_time: datetime
    duration: float
    is_free: bool
    genres: list[Genre]
    primary_audio_type: str
    primary_audio_language: str
    secondary_audio_type: str | None
    secondary_audio_language: str | None

class RecordedPrograms(BaseModel):
    total: int
    recorded_programs: list[RecordedProgram]

# ***** シリーズ *****

class Series(PydanticModel):
    id: int
    title: str
    description: str
    genres: list[Genre]
    broadcast_periods: list[SeriesBroadcastPeriod]
    updated_at: datetime

class SeriesList(BaseModel):
    total: int
    series_list: list[Series]

class SeriesBroadcastPeriod(PydanticModel):
    channel: Channel
    start_date: date
    end_date: date
    recorded_programs: list[RecordedProgram]

# ***** ユーザー *****

class User(PydanticModel):
    id: int
    name: str
    is_admin: bool
    niconico_user_id: int | None
    niconico_user_name: str | None
    niconico_user_premium: bool | None
    twitter_accounts: list[TwitterAccount]  # 追加カラム
    created_at: datetime
    updated_at: datetime

class Users(RootModel[list[User]]):
    pass

# ***** Twitter 連携 *****

class TwitterAccount(PydanticModel):
    id: int
    name: str
    screen_name: str
    icon_url: str
    is_oauth_session: bool  # 追加カラム
    created_at: datetime
    updated_at: datetime

# モデルに関連しない API リクエストの構造を表す Pydantic モデル
## リクエストボティの JSON 構造と一致する

# ***** 録画予約 *****

# 録画予約を追加する
class ReserveAddRequest(BaseModel):
    # 録画予約を追加する番組のネットワーク ID
    network_id: int
    # 録画予約を追加する番組のトランスポートストリーム ID
    transport_stream_id: int
    # 録画予約を追加する番組のサービス ID
    service_id: int
    # 録画予約を追加する番組のイベント ID
    event_id: int
    # 録画設定
    record_settings: RecordSettings

# 録画予約を変更する
class ReserveUpdateRequest(BaseModel):
    # 録画設定
    record_settings: RecordSettings

# キーワード自動予約条件を追加する
class ReserveConditionAddRequest(BaseModel):
    # 番組検索条件
    program_search_condition: ProgramSearchCondition
    # 録画設定
    record_settings: RecordSettings

# キーワード自動予約条件を変更する
## 内容は ReserveConditionAddRequest と同じ (録画予約 ID はパスパラメータから取得するため不要)
class ReserveConditionUpdateRequest(BaseModel):
    # 番組検索条件
    program_search_condition: ProgramSearchCondition
    # 録画設定
    record_settings: RecordSettings

# ***** ユーザー *****

class UserCreateRequest(BaseModel):
    username: str
    password: str

class UserUpdateRequest(BaseModel):
    username: str | None = None
    password: str | None = None

class UserUpdateRequestForAdmin(BaseModel):
    is_admin: bool | None = None

# ***** Twitter 連携 *****

class TwitterPasswordAuthRequest(BaseModel):
    screen_name: str
    password: str

# モデルに関連しない API レスポンスの構造を表す Pydantic モデル
## レスポンスボディの JSON 構造と一致する

# ***** ライブストリーム *****

class LiveStreamStatus(BaseModel):
    status: Literal['Offline', 'Standby', 'ONAir', 'Idling', 'Restart']
    detail: str
    started_at: float
    updated_at: float
    client_count: int

class LiveStreamStatuses(BaseModel):
    Restart: dict[str, LiveStreamStatus]
    Idling: dict[str, LiveStreamStatus]
    ONAir: dict[str, LiveStreamStatus]
    Standby: dict[str, LiveStreamStatus]
    Offline: dict[str, LiveStreamStatus]

# ***** 録画予約 *****

# 以下は EDCB の生のデータモデルをフロントエンドが扱いやすいようモダンに整形し、KonomiTV 独自のプロパティを追加したもの
# EDCB の生のデータモデルは数十年に及ぶ歴史的経緯により複雑怪奇であり、そのままでは非常に扱いにくい
# KonomiTV では以下のモデルを API リクエスト/レスポンスに利用し、サーバー側で EDCB の生のデータモデルと相互に変換している

# 録画予約情報
class Reserve(BaseModel):
    # 録画予約 ID
    id: int
    # 録画予約番組の放送チャンネル
    channel: Channel
    # 録画予約番組の情報
    program: Program
    # 録画予約が現在進行中かどうか
    is_recording_in_progress: bool
    # 録画予約の被り状態: 被りなし (予約可能) / 被ってチューナー足りない予約あり / チューナー足りないため予約できない
    # ref: https://github.com/xtne6f/EDCB/blob/work-plus-s-240212/Common/CommonDef.h#L32-L34
    # ref: https://github.com/xtne6f/EDCB/blob/work-plus-s-240212/Common/StructDef.h#L62
    overlap_status: Literal['NoOverlap', 'HasOverlap', 'CannotReserve']
    # コメント: EPG 予約で自動追加された予約なら "EPG自動予約" と入る
    comment: str
    # 録画予定のファイル名
    ## EDCB からのレスポンスでは配列になっているが、大半の場合は 1 つしか入っていないため単一の値としている
    scheduled_recording_file_name: str
    # 録画設定
    record_settings: RecordSettings

# 録画予約情報のリスト
class Reserves(BaseModel):
    total: int
    reserves: list[Reserve]

# キーワード自動予約条件
class ReserveCondition(BaseModel):
    id: int
    # このキーワード自動予約条件で登録されている録画予約の数
    reserve_count: int
    # 番組検索条件
    program_search_condition: ProgramSearchCondition
    # 録画設定
    record_settings: RecordSettings

# キーワード自動予約条件のリスト
class ReserveConditions(BaseModel):
    total: int
    reserve_conditions: list[ReserveCondition]

# 番組検索条件
class ProgramSearchCondition(BaseModel):
    # 番組検索条件が有効かどうか
    is_enabled: bool = True
    # 検索キーワード
    keyword: str = ''
    # 除外キーワード
    exclude_keyword: str = ''
    # 番組名のみを検索対象とするかどうか
    is_title_only: bool = False
    # 大文字小文字を区別するかどうか
    is_case_sensitive: bool = False
    # あいまい検索を行うかどうか
    is_fuzzy_search_enabled: bool = False
    # 正規表現検索を行うかどうか
    is_regex_search_enabled: bool = False
    # 検索対象を絞り込むチャンネル範囲のリスト
    ## 指定しない場合は None になる
    channel_ranges: list[Channel] | None = None
    # 検索対象を絞り込むジャンルの範囲のリスト
    ## 指定しない場合は None になる
    genre_ranges: list[Genre] | None = None
    # genre_ranges で指定したジャンルを逆に検索対象から除外するかどうか
    is_exclude_genres: bool = False
    # 検索対象を絞り込む放送日時の範囲のリスト
    ## 指定しない場合は None になる
    date_ranges: list[ProgramSearchConditionDate] | None = None
    # date_ranges で指定した放送日時を逆に検索対象から除外するかどうか
    is_exclude_dates: bool = False
    # 番組長で絞り込む最小範囲 (秒)
    ## 指定しない場合は None になる
    duration_range_min: int | None = None
    # 番組長で絞り込む最大範囲 (秒)
    ## 指定しない場合は None になる
    duration_range_max: int | None = None
    # 番組の放送種別で絞り込む: すべて / 無料のみ / 有料のみ
    broadcast_type: Literal['All', 'FreeOnly', 'PaidOnly'] = 'All'
    # 同じ番組名の既存録画との重複チェック: 何もしない / 同じチャンネルのみ対象にする / 全てのチャンネルを対象にする
    ## 同じチャンネルのみ対象にする: 同じチャンネルで同名の番組が既に録画されていれば、新しい予約を無効状態で登録する
    ## 全てのチャンネルを対象にする: 任意のチャンネルで同名の番組が既に録画されていれば、新しい予約を無効状態で登録する
    ## 仕様上予約自体を削除してしまうとすぐ再登録されてしまうので、無効状態で登録することで有効になるのを防いでいるらしい
    duplicate_title_check_scope: Literal['None', 'SameChannelOnly', 'AllChannels'] = 'None'
    # 同じ番組名の既存録画との重複チェックの対象期間 (日単位)
    duplicate_title_check_period_days: int = 6


# 番組検索条件の日付範囲
class ProgramSearchConditionDate(BaseModel):
    # 検索開始曜日 (0: 日曜日, 1: 月曜日, 2: 火曜日, 3: 水曜日, 4: 木曜日, 5: 金曜日, 6: 土曜日)
    ## 文字列にした方がわかりやすいとも思ったが、day.js が数値で曜日を扱うため数値で統一する
    start_day_of_week: int
    # 検索開始時刻 (時)
    start_hour: int
    # 検索開始時刻 (分)
    start_minute: int
    # 検索終了曜日 (0: 日曜日, 1: 月曜日, 2: 火曜日, 3: 水曜日, 4: 木曜日, 5: 金曜日, 6: 土曜日)
    ## 文字列にした方がわかりやすいとも思ったが、day.js が数値で曜日を扱うため数値で統一する
    end_day_of_week: int
    # 検索終了時刻 (時)
    end_hour: int
    # 検索終了時刻 (分)
    end_minute: int

# 録画設定
## 現実的にほとんど使わないため UI からは設定できない値も含まれる (録画設定変更時に意図せず設定値が抜け落ちることは避けたい)
class RecordSettings(BaseModel):
    # 録画予約が有効かどうか
    is_enabled: bool = True
    # 録画モード: 全サービス / 全サービス (デコードなし) / 指定サービスのみ / 指定サービスのみ (デコードなし) / 視聴
    # 通常の用途では「指定サービスのみ」以外はまず使わない
    ## ref: https://github.com/xtne6f/EDCB/blob/work-plus-s-240212/Common/CommonDef.h#L26-L30
    ## ref: https://github.com/xtne6f/EDCB/blob/work-plus-s-240212/Document/Readme_Mod.txt#L264-L266
    record_mode: Literal['AllService', 'AllServiceWithoutDecoding', 'SpecifiedService', 'SpecifiedServiceWithoutDecoding', 'View'] = 'SpecifiedService'
    # 録画予約の優先度: 1 ~ 5 の数値で数値が大きいほど優先度が高い
    priority: int = 3
    # 録画開始マージン (秒) / デフォルト設定に従う場合は None
    recording_start_margin: int | None = None
    # 録画終了マージン (秒) / デフォルト設定に従う場合は None
    recording_end_margin: int | None = None
    # 録画後の動作モード: デフォルト設定に従う / 何もしない / スタンバイ / 休止 / シャットダウン / 再起動
    post_recording_mode: Literal['Default', 'Nothing', 'Standby', 'Suspend', 'Shutdown', 'Reboot'] = 'Default'
    # 録画後に実行する bat ファイルのパス / 指定しない場合は None
    post_recording_bat_file_path: str | None = None
    # 保存先の録画フォルダのパスのリスト
    ## 指定されない場合はデフォルトの録画フォルダに順に保存される
    ## UI 上では単一の録画フォルダしか指定できない (複数のフォルダに同じ内容を保存するユースケースが皆無なため)
    recording_folders: list[str] = []
    # イベントリレーの追従を行うかどうか
    ## UI 上では非表示 (新規追加時は True で固定)
    is_event_relay_follow_enabled: bool = True
    # 「ぴったり録画」(録画マージンののりしろを残さず本編のみを正確に録画する？) を行うかどうか
    ## 番組は EPG 上の開始時刻よりも早く放送開始することがあるため、基本推奨されないらしい
    ## UI 上では非表示 (新規追加時は False で固定)
    is_exact_recording_enabled: bool = False
    # 録画対象のチャンネルにワンセグ放送が含まれる場合、ワンセグ放送を別ファイルに同時録画するかどうか
    ## UI 上では非表示 (新規追加時は False で固定)
    is_oneseg_separate_output_enabled: bool = False
    # 同一チャンネルで時間的に隣接した録画予約がある場合に、それらを同一の録画ファイルに続けて出力するかどうか
    ## UI 上では非表示 (新規追加時は False で固定)
    is_sequential_recording_in_single_file_enabled: bool = False
    # 字幕データを録画するかどうか (Default のとき、デフォルト設定に従う)
    ## UI 上では非表示 (新規追加時は Default で固定)
    caption_recording_mode: Literal['Default', 'Enable', 'Disable'] = 'Default'
    # データ放送を録画するかどうか (Default のとき、デフォルト設定に従う)
    ## UI 上では非表示 (新規追加時は Default で固定)
    data_broadcasting_recording_mode: Literal['Default', 'Enable', 'Disable'] = 'Default'
    # チューナーを強制指定する際のチューナー ID / 自動選択の場合は None
    # UI 上では非表示 (新規追加時は None で固定)
    forced_tuner_id: int | None = None

# ***** データ放送 *****

class DataBroadcastingInternetStatus(BaseModel):
    success: bool
    ip_address: str | None
    response_time_milliseconds: int | None

# ***** ニコニコ実況連携 *****

class JikkyoComment(BaseModel):
    time: float
    type: Literal['top', 'right', 'bottom']
    size: Literal['big', 'medium', 'small']
    color: str
    author: str
    text: str

class JikkyoComments(BaseModel):
    is_success: bool
    comments: list[JikkyoComment]
    detail: str

class JikkyoSession(BaseModel):
    is_success: bool
    audience_token: str | None = None
    detail: str

class ThirdpartyAuthURL(BaseModel):
    authorization_url: str

# ***** Twitter 連携 *****

class Tweet(BaseModel):
    id: str
    created_at: datetime
    user: TweetUser
    text: str
    lang: str
    via: str
    image_urls: list[str] | None
    movie_url: str | None
    retweet_count: int
    retweeted: bool
    favorite_count: int
    favorited: bool
    retweeted_tweet: Union['Tweet', None]
    quoted_tweet: Union['Tweet', None]

class TweetUser(BaseModel):
    id: str
    name: str
    screen_name: str
    icon_url: str

class TwitterAPIResult(BaseModel):
    is_success: bool
    detail: str

class PostTweetResult(TwitterAPIResult):
    tweet_url: str

class TimelineTweetsResult(TwitterAPIResult):
    next_cursor_id: str
    previous_cursor_id: str
    tweets: list[Tweet]

# ***** ユーザー *****

class UserAccessToken(BaseModel):
    access_token: str
    token_type: str

# ***** バージョン情報 *****

class VersionInformation(BaseModel):
    version: str
    latest_version: str | None
    environment: Literal['Windows', 'Linux', 'Linux-Docker', 'Linux-ARM']
    backend: Literal['EDCB', 'Mirakurun']
    encoder: Literal['FFmpeg', 'QSVEncC', 'NVEncC', 'VCEEncC', 'rkmppenc']

# UserWarning を再度有効化
warnings.filterwarnings('default', category=UserWarning)
