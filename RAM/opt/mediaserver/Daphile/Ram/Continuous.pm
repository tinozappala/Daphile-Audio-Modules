package Daphile::Ram::Continuous;
use strict;use warnings;
use IO::Socket::UNIX;use IO::Select;use Socket qw(SOCK_STREAM);
use JSON::XS ();use Time::HiRes qw(time);use Scalar::Util qw(weaken);
use Digest::SHA qw(sha256_hex);
use Slim::Utils::Timers;
use Slim::Utils::Log;
my $log=Slim::Utils::Log::logger('player.source');

my %held;my $sequence=0;my $timer={};my $scheduled=0;
our $REQUIRED='/run/daphile-ram/continuous-required';
our $SOCKET='/run/daphile-ram/control.sock';

sub enabled {return -f $REQUIRED || -f '/run/daphile-ram/live-switching'}
sub schedule {
    return if $scheduled;
    $scheduled=1;Slim::Utils::Timers::setTimer($timer,time()+.1,\&tick);
}
sub connect_request {
    my($entry,$value)=@_;
    my $socket=IO::Socket::UNIX->new(Type=>SOCK_STREAM,Peer=>$SOCKET,Timeout=>.1);
    die "RAM_SERVICE_UNAVAILABLE" unless $socket;
    $socket->blocking(0);
    $entry->{io_op}=$value->{op};
    $entry->{socket}=$socket;$entry->{out}=JSON::XS->new->utf8->encode($value)."\n";
    $entry->{in}='';$entry->{sent}=0;$entry->{io_deadline}=time()+2;
}
sub response {
    my($entry)=@_;my $socket=$entry->{socket};my $poll=IO::Select->new($socket);
    die "RAM_SERVICE_TIMEOUT" if time()>$entry->{io_deadline};
    if($entry->{sent}<length($entry->{out}) && $poll->can_write(0)){
        my $n=syswrite($socket,$entry->{out},length($entry->{out})-$entry->{sent},$entry->{sent});
        die "RAM_SERVICE_WRITE" unless defined($n)&&$n>0;$entry->{sent}+=$n;
    }
    return unless $poll->can_read(0);
    my $n=sysread($socket,my $part,16384-length($entry->{in}));
    die "RAM_SERVICE_READ" unless defined($n)&&$n>0;
    $entry->{in}.=$part;die "RAM_SERVICE_FRAME" if length($entry->{in})>=16384;
    return unless index($entry->{in},"\n")>=0;
    close($socket);delete $entry->{socket};
    my $reply=JSON::XS->new->decode($entry->{in});
    die($reply->{error}||'RAM_OPERATION_REFUSED') unless $reply->{ok};
    return $reply->{state};
}
sub prepare {
    my($song,$success,$failure,$valid,$preload_seconds)=@_;
    $preload_seconds=120 unless defined $preload_seconds;
    unless(enabled()){$success->();return;}
    my $token=sha256_hex($$.'-'.++$sequence.'-'.time().'-'.rand());
    my $entry={song=>$song,success=>$success,failure=>$failure,url=>$song->currentTrack()->url,
               token=>$token,deadline=>time()+$preload_seconds,preload_seconds=>$preload_seconds,phase=>'ensure',valid=>$valid};
    weaken($entry->{song});$held{$token}=$entry;
    $song->pluginData('ram_continuous_token',$token);schedule();
}
sub fail {
    my($entry,$reason)=@_;
    $reason=$reason=~/(RAM_[A-Z_]+)/?$1:'RAM_NATIVE_GATE_ERROR';
    $log->error('RAM continuous gate: '.$reason);
    $entry->{error}=$reason;
    my $song=$entry->{song};my $failure=delete $entry->{failure};delete $entry->{success};
    $entry->{phase}='release';
    if($song){
        my %message=(RAM_FILE_EXCEEDS_CACHE=>'Il brano supera la capienza della cache RAM',
            RAM_LOCAL_FILE_REQUIRED=>'Questa sorgente non e un file locale o NAS',
            RAM_SOURCE_UNAVAILABLE=>'La sorgente non e raggiungibile',
            RAM_PRELOAD_TIMEOUT=>'Il precaricamento RAM non e terminato',
            RAM_NOT_READY=>'La cache RAM non e pronta');
        $failure->($message{$reason}||'Riproduzione arrestata: copia RAM non disponibile')if $failure;
        $song->master()->execute(['stop']);
    }
}
sub tick {
    $scheduled=0;
    for my $token(keys %held){
        my $entry=$held{$token};my $song=$entry->{song};
        # Native Song lifetime covers both playing and buffered next tracks,
        # including transcoder subprocesses which open paths independently.
        # Native seek calls _Stop then reuses its FINISHED Song in _Stream.
        # Pending readiness belongs to that callback id, not to Song::isActive.
        $entry->{phase}='release' if !$song
            ||($entry->{phase}eq'ensure'&&$entry->{valid}&&!$entry->{valid}->())
            ||($entry->{phase}eq'held'&&(!$song->isActive()||$song->status()==0));
        if($entry->{phase}eq'held'){next;}
        if($entry->{phase}eq'release'){
            delete $entry->{success};delete $entry->{failure};delete $entry->{valid};
            if($entry->{socket}&&$entry->{io_op}ne'release'){close(delete $entry->{socket});}
        }
        eval {
            if(!$entry->{socket}){
                if($entry->{phase}eq'ensure'&&!$entry->{waiting_space}&&time()>$entry->{deadline}){fail($entry,'RAM_PRELOAD_TIMEOUT');}
                connect_request($entry,{op=>$entry->{phase},token=>$token,url=>$entry->{url},error=>$entry->{error}});
            }
            my $state=response($entry);
            if(!$state){1;}
            elsif($entry->{phase}eq'release'){delete $held{$token};}
            elsif($state->{state}eq'READY'||$state->{state}eq'OFF'){
                if($state->{state}eq'READY'){
                    my $handler=$song->currentTrackHandler();
                    my $path=$handler->can('pathFromFileURL')?$handler->pathFromFileURL($entry->{url}):undef;
                    my @s=defined($path)?stat($path):();
                    die "RAM_NATIVE_VIEW_MISMATCH" unless @s&&$s[0]==$state->{identity}[0]&&$s[1]==$state->{identity}[1];
                    $song->pluginData('ram_continuous_identity',$state->{identity});
                }
                $entry->{phase}='held';my $success=delete $entry->{success};delete $entry->{failure};delete $entry->{valid};$success->();
            }elsif($state->{state}eq'WAITING_SPACE'||$state->{state}eq'WAITING_SWITCH'){
                # A playing/paused Song can legitimately occupy the cache for
                # longer than a file-transfer budget. Its verified native pin
                # owns this separate phase; callback cancellation still works.
                $entry->{waiting_space}=1;
            }elsif($state->{state}eq'PREPARING'&&delete $entry->{waiting_space}){
                $entry->{deadline}=time()+$entry->{preload_seconds};
            }elsif($state->{state}eq'ERROR'){fail($entry,$state->{error}||'RAM_PRELOAD_FAILED');}
            1;
        }or do {
            my $error=$@||'RAM_SERVICE_UNAVAILABLE';
            if($entry->{socket}){close(delete $entry->{socket});}
            # Releases retry idempotently; a service restart retains the pins.
            fail($entry,$error)if $entry->{phase}ne'release';
        };
    }
    schedule()if keys %held;
}
sub ready {
    my($song)=@_;return 1 unless enabled();
    my $identity=$song->pluginData('ram_continuous_identity');return 0 unless $identity;
    my $handler=$song->currentTrackHandler();return 0 unless $handler->can('pathFromFileURL');
    my $path=$handler->pathFromFileURL($song->currentTrack()->url);my @s=stat($path);
    return @s&&$s[0]==$identity->[0]&&$s[1]==$identity->[1];
}
1;
