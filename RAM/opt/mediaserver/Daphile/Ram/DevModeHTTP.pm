package Daphile::Ram::DevModeHTTP;
use strict;use warnings;
use JSON::XS ();use IO::Socket::UNIX;use IO::Select;use Socket qw(SOCK_STREAM sockaddr_in inet_ntoa);
use URI::Escape qw(uri_unescape);use Digest::SHA qw(sha256_hex);use Time::HiRes qw(time);
my $ROUTE='/settings/server/ram-nextboot.html';
sub need {die "RAM_REQUEST_REFUSED\n" unless $_[0]}
sub root_request {
    my($value)=@_;my $s=IO::Socket::UNIX->new(Type=>SOCK_STREAM,Peer=>'/run/daphile-ram/control.sock',Timeout=>20)or die "RAM_SERVICE_UNAVAILABLE\n";
    $s->autoflush(1);print $s JSON::XS->new->canonical->encode($value)."\n";
    my $raw='';my $deadline=time()+20;
    my $poll=IO::Select->new($s);
    while(index($raw,"\n")<0){my $left=$deadline-time();need($left>0 && length($raw)<16384 && $poll->can_read($left));my $got=sysread($s,my $part,16384-length($raw));need(defined($got)&&$got>0);$raw.=$part;}
    close($s);my $response=JSON::XS->new->decode($raw);die "RAM_OPERATION_REFUSED\n" unless $response->{ok};return $response->{state};
}
sub html {my($v)=@_;$v=''unless defined$v;$v=~s/&/&amp;/g;$v=~s/</&lt;/g;$v=~s/>/&gt;/g;$v=~s/"/&quot;/g;return$v}
sub fields {
    my($text)=@_;need(length($text)<=4096);my%v;
    for my $pair(split /&/,$text){need($pair=~/\A([^=]+)=(.*)\z/s);my($k,$x)=($1,$2);for($k,$x){tr/+/ /;$_=uri_unescape($_);need(!/[\x00\r\n]/)}need(!exists$v{$k});$v{$k}=$x;}return\%v;
}
sub respond {
    my($socket,$response,$code,$body)=@_;$response->code($code);$response->header('Connection'=>'close','Cache-Control'=>'no-store','X-Content-Type-Options'=>'nosniff');$response->content_type('text/html; charset=utf-8');Slim::Web::HTTP::addHTTPResponse($socket,$response,\$body,1,0);
}
sub handle {
    my($socket,$response)=@_;my $ok=eval {
        my $request=$response->request;my $post=$request->method eq 'POST';need($post||$request->method eq 'GET');
        need(!Slim::Utils::Prefs::preferences('server')->get('authorize'));
        my($port,$address)=sockaddr_in(getsockname($socket));my $expected=inet_ntoa($address).':'.$port;my $host=$request->header('Host');need(defined($host)&&($host eq $expected||$port==80&&$host eq inet_ntoa($address)));
        if($post){my $origin=$request->header('Origin');my $referer=$request->header('Referer');need(defined($origin)?$origin eq 'http://'.$host:defined($referer)&&index($referer,'http://'.$host.'/')==0);}
        my $state=root_request({op=>'status'});my $saved='';my $playing=grep { $_->connected && Slim::Player::Source::playmode($_) eq 'play' } Slim::Player::Client::clients();
        if($post){
            need(!$playing);
            need(($request->header('Content-Type')||'')=~m!\Aapplication/x-www-form-urlencoded(?:;|\z)!);my $v=fields($request->content);
            need(defined($v->{pageAntiCSRFToken})&&$v->{pageAntiCSRFToken}eq Slim::Web::HTTP::CSRF->makePageToken($request));
            need(defined($v->{expected_version})&&$v->{expected_version}=~/\A(?:0|[1-9][0-9]*)\z/&&defined($v->{requested_ram})&&$v->{requested_ram}=~/\A[01]\z/&&defined($v->{request_id})&&$v->{request_id}=~/\A[a-f0-9]{64}\z/);
            $state=root_request({op=>'set',expected_version=>0+$v->{expected_version},requested_ram=>$v->{requested_ram}?JSON::XS::true:JSON::XS::false,request_id=>$v->{request_id}});$saved='<p>Richiesta ricevuta. Applicazione immediata in corso.</p>' if $state->{live}{busy};
        }
        my $token=Slim::Web::HTTP::CSRF->makePageToken($request);need(defined($token)&&$token=~/\A[0-9a-f]{32}\z/);my $id=sha256_hex(time().'-'.$$.'-'.rand());
        my $body='<html><head>'.($state->{live}{busy}?'<meta http-equiv="refresh" content="2">':'').'</head><body><h1>RAM</h1>'.$saved.'<p>RAM richiesta (salvata): '.($state->{requested_ram}?'ON':'OFF').'</p><p>Cache RAM reale attiva: '.($state->{effective_ram}?'ON':'OFF').'</p>';
        if(my $c=$state->{continuous}){
            my %labels=(READY=>'Pronta',PREPARING=>'Precaricamento',WAITING_SPACE=>'In attesa di spazio',ERROR=>'Operazione non completata');
            my %errors=(RAM_FILE_EXCEEDS_CACHE=>'Il brano supera la capienza della cache RAM.',RAM_LOCAL_FILE_REQUIRED=>'Questa sorgente non e un file locale o NAS.',RAM_SOURCE_UNAVAILABLE=>'La sorgente non e raggiungibile.',RAM_PRELOAD_TIMEOUT=>'Il precaricamento non e terminato: verificare la sorgente e lo spazio RAM disponibile.');
            $body.='<p>RAM continua: '.($c->{enabled}?'ON':'OFF').'</p><p>Stato: '.html($labels{$c->{state}}||'In aggiornamento').'</p>';
            $body.='<p>Cache: '.html($c->{files}).' file, '.sprintf('%.1f',($c->{bytes}||0)/1048576).' / '.sprintf('%.1f',($c->{limit_bytes}||0)/1048576).' MiB</p>';
            $body.='<p>Precaricamenti in attesa: '.html($c->{pending}).'</p>';
            $body.='<p>Riproduzione arrestata. '.html($errors{$c->{error}}||'La copia RAM non e disponibile.').'</p>' if $c->{error};
            $body.='<p>I nuovi brani locali o NAS vengono copiati nel formato originale prima della riproduzione. Nessun riavvio per cambiare brani o playlist. File oltre la capienza e flussi di rete non memorizzabili vengono segnalati senza lettura diretta dalla sorgente.</p>';
        }
        if(my $live=$state->{live}){
            my %phases=(IDLE=>'Pronta',QUEUED=>'Richiesta accodata',READING_PLAYER_STATE=>'Lettura stato del player',WAITING_PLAYER_READY=>'Attesa del precaricamento corrente',PAUSING_PLAYER=>'Pausa del flusso',CLOSING_PREVIOUS_STREAM=>'Rilascio del flusso precedente',WAITING_FOR_COPY_WORKER=>'Attesa del worker RAM',WAITING_FOR_STREAM_RELEASE=>'Attesa del rilascio dei brani',PREPARING_RAM=>'Attivazione RAM',RESTORING_ORIGINAL_PATHS=>'Disattivazione RAM',PRELOADING_CURRENT_TRACK=>'Copia del brano corrente',RESUMING_PLAYER=>'Ripresa della riproduzione',COMPLETE=>'Applicazione completata',FAILED=>'Applicazione non completata');
            $body.='<p>Comando RAM: '.html($phases{$live->{phase}}||'In aggiornamento').'</p>';
            $body.='<p>ON/OFF si applica in pausa o a player fermo, senza riavviare il sistema o il Media Server. La scelta resta salvata anche dopo il riavvio.</p>';
            $body.='<p>Metti prima in pausa il brano per cambiare RAM. I pulsanti sono disabilitati durante riproduzione e precaricamento.</p>' if $playing;
            $body.='<p>Lo stato del player e cambiato durante il comando: la nuova scelta dell utente e stata conservata.</p>' if $live->{user_state_changed};
        }
        my %recovery_errors=(RAM_PLAYER_PLAYING_MUST_PAUSE=>'Metti prima in pausa il brano per cambiare RAM.',RAM_MULTIPLE_ACTIVE_PLAYERS=>'Sono presenti piu player attivi. Ferma gli altri player prima del cambio RAM.',RAM_PAUSE_CANNOT_RELEASE=>'Questa sorgente non consente il cambio in pausa. Ferma il player e riprova.',RAM_USER_STATE_CHANGED=>'Lo stato del player e cambiato. La nuova scelta e stata conservata.',RAM_SWITCH_INTERRUPTED=>'Il comando precedente e stato interrotto. Rileggi lo stato e riprova.',RAM_SOURCE_UNAVAILABLE=>'La sorgente non e raggiungibile.',RAM_FILE_EXCEEDS_CACHE=>'Il brano supera la capienza della cache RAM.');
        $body.='<p>Recupero: '.html($recovery_errors{$state->{error}}||'Operazione non completata. Rileggere lo stato prima di riprovare.').'</p>'if $state->{error};
        $body.='<p>Coda: '.html($state->{queue_restore}).'</p>'if $state->{queue_restore};
        $body.='<form method="post" action="'.$ROUTE.'"><input type="hidden" name="pageAntiCSRFToken" value="'.$token.'"><input type="hidden" name="expected_version" value="'.html($state->{version}).'"><input type="hidden" name="request_id" value="'.$id.'"><button name="requested_ram" value="1"'.($state->{live}{busy}||$playing?' disabled':'').'>ON</button><button name="requested_ram" value="0"'.($state->{live}{busy}||$playing?' disabled':'').'>OFF</button></form></body></html>';
        respond($socket,$response,200,$body);1;
    };unless($ok){respond($socket,$response,409,'<html><body><h1>RAM</h1><p>Operazione non completata. Rileggere lo stato prima di riprovare.</p></body></html>');}
}
sub install {
    Slim::Web::Pages->addRawFunction($ROUTE,\&handle);
    Slim::Utils::Strings::storeExtraStrings([
        { token => 'DAPHILE_RAM_SETTINGS', strings => { EN => 'RAM', IT => 'RAM' } }
    ]);
    Slim::Web::Pages->addPageLinks('setup', { 'DAPHILE_RAM_SETTINGS' => substr($ROUTE,1) });
    return;
}
1;
