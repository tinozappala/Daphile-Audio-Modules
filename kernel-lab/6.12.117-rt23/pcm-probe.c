/* SPDX-License-Identifier: MIT
 * Tino Zappala, Milano - 2026
 */
#define _POSIX_C_SOURCE 200809L
#include <alloca.h>
#include <alsa/asoundlib.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <errno.h>

struct ctx { snd_pcm_t *pcm; unsigned period; atomic_int stop, error; };
static double now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC_RAW,&t);
    return t.tv_sec*1000.0+t.tv_nsec/1000000.0;
}
static int configure(snd_pcm_t *h,unsigned rate,unsigned period,unsigned buffer) {
    snd_pcm_hw_params_t *p; snd_pcm_hw_params_alloca(&p);
    snd_pcm_sw_params_t *s; snd_pcm_sw_params_alloca(&s);
    snd_pcm_uframes_t per=period,buf=buffer; int dir=0,rc;
#define CHECK(x) do {if ((rc=(x))<0) return rc;} while(0)
    CHECK(snd_pcm_hw_params_any(h,p));
    CHECK(snd_pcm_hw_params_set_access(h,p,SND_PCM_ACCESS_RW_INTERLEAVED));
    CHECK(snd_pcm_hw_params_set_format(h,p,SND_PCM_FORMAT_S32_LE));
    CHECK(snd_pcm_hw_params_set_channels(h,p,2));
    CHECK(snd_pcm_hw_params_set_rate(h,p,rate,0));
    CHECK(snd_pcm_hw_params_set_period_size_near(h,p,&per,&dir));
    if(per!=period) return -EINVAL;
    CHECK(snd_pcm_hw_params_set_buffer_size_near(h,p,&buf));
    CHECK(snd_pcm_hw_params(h,p));
    CHECK(snd_pcm_hw_params_get_period_size(p,&per,&dir));
    CHECK(snd_pcm_hw_params_get_buffer_size(p,&buf));
    if(per!=period || buf!=buffer) return -EINVAL;
    CHECK(snd_pcm_sw_params_current(h,s));
    CHECK(snd_pcm_sw_params_set_avail_min(h,s,period));
    CHECK(snd_pcm_sw_params_set_start_threshold(h,s,1));
    CHECK(snd_pcm_sw_params(h,s));
    return snd_pcm_prepare(h);
#undef CHECK
}
static void *writer(void *arg) {
    struct ctx *x=arg;
    int32_t *b=calloc(x->period*2,sizeof(*b));
    uint32_t seq=1;
    if (!b) {atomic_store(&x->error,-ENOMEM);return NULL;}
    while(!atomic_load(&x->stop)) {
        for(unsigned i=0;i<x->period;i++){b[2*i]=(int32_t)(seq+i);b[2*i+1]=-(int32_t)(seq+i);}
        snd_pcm_sframes_t n=snd_pcm_writei(x->pcm,b,x->period);
        if(n==-EAGAIN || n==0){snd_pcm_wait(x->pcm,20);continue;}
        if(n<0){atomic_store(&x->error,(int)n);break;}
        seq+=(uint32_t)n;
    }
    free(b);return NULL;
}
static int cmp(const void *a,const void *b){double x=*(const double*)a,y=*(const double*)b;return (x>y)-(x<y);}
int main(int argc,char **argv){
    if(argc!=5){fprintf(stderr,"usage: pcm-probe rate period seconds substream\n");return 2;}
    unsigned rate=atoi(argv[1]),period=atoi(argv[2]),seconds=atoi(argv[3]),sub=atoi(argv[4]);
    if(!rate||rate>384000||!period||period>4096||!seconds||seconds>30||sub>7)return 2;
    unsigned playbuf=((7680+period-1)/period)*period,capbuf=((rate/5+period-1)/period)*period;
    size_t blocks=(size_t)rate*seconds/period;
    if(blocks<5)return 2;
    char pn[64],cn[64];snprintf(pn,sizeof pn,"hw:Loopback,0,%u",sub);snprintf(cn,sizeof cn,"hw:Loopback,1,%u",sub);
    snd_pcm_t *p=NULL,*c=NULL;int rc,created=0;
    int32_t *buf=calloc(period*2,sizeof(*buf));
    double *gaps=calloc(blocks,sizeof(*gaps));
    struct ctx x={0};pthread_t th;
    uint64_t active=0,zeros=0,errors=0,partial=0,late=0;uint32_t prev=0;
    size_t ng=0;double last=0,max=0,nominal=1000.0*period/rate;
    if(!buf||!gaps){rc=-ENOMEM;goto out;}
    if((rc=snd_pcm_open(&p,pn,SND_PCM_STREAM_PLAYBACK,0))<0)goto out;
    if((rc=snd_pcm_open(&c,cn,SND_PCM_STREAM_CAPTURE,0))<0)goto out;
    if((rc=configure(p,rate,period,playbuf))<0)goto out;
    if((rc=configure(c,rate,period,capbuf))<0)goto out;
    if((rc=snd_pcm_nonblock(p,1))<0)goto out;
    if((rc=snd_pcm_start(c))<0)goto out;
    x.pcm=p;x.period=period;atomic_init(&x.stop,0);atomic_init(&x.error,0);
    if((rc=pthread_create(&th,NULL,writer,&x))!=0){rc=-rc;goto out;}
    created=1;
    for(size_t j=0;j<blocks;j++){
        unsigned got=0;
        while(got<period){
            snd_pcm_sframes_t n=snd_pcm_readi(c,buf+2*got,period-got);
            if(n<0){rc=(int)n;goto out;}
            if(!n)continue;
            if((unsigned)n<period-got)partial++;
            got+=(unsigned)n;
        }
        double t=now_ms();
        if(last && j>4){double d=t-last;gaps[ng++]=d;if(d>max)max=d;if(d>2*nominal)late++;}
        last=t;
        for(unsigned i=0;i<period;i++){
            int32_t a=buf[2*i],b=buf[2*i+1];
            if(!a&&!b){zeros++;if(active)errors++;continue;}
            if(a<=0 || (int64_t)a+(int64_t)b!=0){errors++;continue;}
            if(active && (uint32_t)a!=prev+1)errors++;
            prev=(uint32_t)a;active++;
        }
        if(atomic_load(&x.error)){rc=atomic_load(&x.error);goto out;}
    }
    rc=0;
out:
    if(created){atomic_store(&x.stop,1);pthread_join(th,NULL);if(!rc && atomic_load(&x.error))rc=atomic_load(&x.error);}
    if(c){snd_pcm_drop(c);snd_pcm_close(c);}
    if(p){snd_pcm_drop(p);snd_pcm_close(p);}
    if(ng)qsort(gaps,ng,sizeof(*gaps),cmp);
    int pass=rc==0 && errors==0 && active>=(uint64_t)rate*seconds*8/10 && ng>0;
    printf("PCM_RESULT rate=%u period=%u sub=%u seconds=%u status=%s rc=%d active=%llu zero=%llu integrity_errors=%llu partial_reads=%llu intervals=%zu p50_ms=%.6f p95_ms=%.6f max_ms=%.6f gap_gt_2period=%llu\n",rate,period,sub,seconds,pass?"PASS":"FAIL",rc,(unsigned long long)active,(unsigned long long)zeros,(unsigned long long)errors,(unsigned long long)partial,ng,ng?gaps[ng/2]:0,ng?gaps[(ng-1)*95/100]:0,max,(unsigned long long)late);
    if(rc<0)fprintf(stderr,"ALSA: %s\n",snd_strerror(rc));
    free(buf);free(gaps);return pass?0:1;
}
