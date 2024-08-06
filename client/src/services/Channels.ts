
import APIClient from '@/services/APIClient';
import { IProgram, IProgramDefault } from '@/services/Programs';


/** チャンネルタイプの型 */
export type ChannelType = 'GR' | 'BS' | 'CS' | 'CATV' | 'SKY' | 'STARDIGIO';

// チャンネルタイプの型 (実際のチャンネルリストに表示される表現)
export type ChannelTypePretty = 'ピン留め' | '地デジ' | 'BS' | 'CS' | 'CATV' | 'SKY' | 'StarDigio';

/** チャンネル情報を表すインターフェイス */
export interface IChannel {
    id: string;
    display_channel_id: string;
    network_id: number;
    service_id: number;
    transport_stream_id: number | null;
    remocon_id: number;
    channel_number: string;
    type: ChannelType;
    name: string;
    jikkyo_force: number | null;
    is_subchannel: boolean;
    is_radiochannel: boolean;
    is_watchable: boolean,
}

/** 現在放送中のチャンネル情報を表すインターフェイス */
export interface ILiveChannel extends IChannel {
    // 以下はすべて動的に生成される TV ライブストリーミング用の追加カラム
    is_display: boolean;
    viewer_count: number;
    program_present: IProgram | null;
    program_following: IProgram | null;
}

/** 現在放送中のチャンネル情報を表すインターフェイスのデフォルト値 */
export const ILiveChannelDefault: ILiveChannel = {
    id: 'NID0-SID0',
    display_channel_id: 'gr000',
    network_id: 0,
    service_id: 0,
    transport_stream_id: null,
    remocon_id: 0,
    channel_number: '---',
    type: 'GR',
    name: '取得中…',
    jikkyo_force: null,
    is_subchannel: false,
    is_radiochannel: false,
    is_watchable: true,
    is_display: true,
    viewer_count: 0,
    program_present: IProgramDefault,
    program_following: IProgramDefault,
};

/** すべてのチャンネルタイプの現在放送中のチャンネルの情報を表すインターフェイス */
export interface ILiveChannelsList {
    GR: ILiveChannel[];
    BS: ILiveChannel[];
    CS: ILiveChannel[];
    CATV: ILiveChannel[];
    SKY: ILiveChannel[];
    STARDIGIO: ILiveChannel[];
}

/** ニコニコ実況の WebSocket API の情報を表すインターフェイス */
export interface IJikkyoWebSocketInfo {
    websocket_url: string | null;
    is_nxjikkyo_exclusive: boolean;
}

/** ニコニコ実況へのコメント送信リクエストを表すインターフェイス */
export interface IJikkyoSendCommentRequest {
    text: string;
    color: string;
    position: string;
    size: string;
    vpos: number;
}

/** ニコニコ実況へのコメント送信結果を表すインターフェイス */
export interface IJikkyoSendCommentResult {
    is_success: boolean;
    detail: string;
}

class Channels {

    /**
     * すべてのチャンネルの情報を取得する
     * @return すべてのチャンネルの情報
     */
    static async fetchAll(): Promise<ILiveChannelsList | null> {

        // API リクエストを実行
        const response = await APIClient.get<ILiveChannelsList>('/channels');

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'チャンネル情報を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * 指定したチャンネルの情報を取得する
     * 現状、処理の見直しにより使用されていない
     * @param channel_id チャンネル ID (id or display_channel_id)
     * @return 指定したチャンネルの情報
     */
    static async fetch(channel_id: string): Promise<ILiveChannel | null> {

        // API リクエストを実行
        const response = await APIClient.get<ILiveChannel>(`/channels/${channel_id}`);

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'チャンネル情報を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * 指定されたチャンネルに対応する、ニコニコ実況からコメントを受信するための WebSocket API の情報を取得する
     * @param channel_id チャンネル ID (id or display_channel_id)
     * @return ニコニコ実況からコメントを受信するための WebSocket API の情報
     */
    static async fetchJikkyoWebSocketInfo(channel_id: string): Promise<IJikkyoWebSocketInfo | null> {

        // API リクエストを実行
        const response = await APIClient.get<IJikkyoWebSocketInfo>(`/channels/${channel_id}/jikkyo`);

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'コメント受信用 WebSocket API の情報を取得できませんでした。');
            return null;
        }

        return response.data;
    }

    /**
     * 指定されたチャンネルに対応するニコニコ実況チャンネルにコメントを送信する
     * @param channel_id チャンネル ID (id or display_channel_id)
     * @param comment 送信するコメントの内容
     * @return コメント送信結果
     */
    static async sendJikkyoComment(channel_id: string, comment: IJikkyoSendCommentRequest): Promise<IJikkyoSendCommentResult | null> {

        // API リクエストを実行
        const response = await APIClient.post<IJikkyoSendCommentResult>(`/channels/${channel_id}/jikkyo/comment`, comment);

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'ニコニコ実況へのコメント送信に失敗しました。');
            return null;
        }

        return response.data;
    }
}

export default Channels;
