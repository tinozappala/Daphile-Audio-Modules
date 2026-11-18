#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/avutil.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    AVFormatContext *format = NULL;
    if (argc != 2 || avformat_open_input(&format, argv[1], NULL, NULL) < 0)
        return 1;
    if (avformat_find_stream_info(format, NULL) < 0 || !format->nb_streams)
        return 2;
    const AVCodecParameters *codec = format->streams[0]->codecpar;
    printf("libavutil=%s audio_rate=%d channels=%d\n", av_version_info(),
           codec->sample_rate, codec->ch_layout.nb_channels);
    int ok = codec->sample_rate == 48000 && codec->ch_layout.nb_channels == 2 &&
             avcodec_find_decoder(AV_CODEC_ID_FLAC);
    avformat_close_input(&format);
    return ok ? 0 : 3;
}
