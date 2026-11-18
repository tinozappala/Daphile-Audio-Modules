use strict;
use warnings;
use Config;
use DynaLoader;
use File::Temp qw(tempdir);
my ($wav,$png,$list)=@ARGV;
die 'version' unless $^V eq v5.40.5 && $Config{version} eq '5.40.5';
die 'ABI' unless $Config{archname} eq 'x86_64-linux-thread-multi' &&
 $Config{useithreads} eq 'define' && $Config{usemultiplicity} eq 'define' &&
 $Config{use64bitint} eq 'define' && $Config{libperl} eq 'libperl.so.5.40.2';
open my $in, '<', $list or die $!;
my @modules=grep length,map { chomp; $_ } <$in>;
for my $module (@modules) {
 my $file=$module;
 $file=~s{::}{/}g;
 require "$file.pm";
 print "IMPORT $module\n";
}
my @fixture_so=sort grep {/lms-CPAN/} @DynaLoader::dl_shared_objects;
die "Expected 15 unchanged fixture .so, got ".scalar(@fixture_so) unless @fixture_so==15;
for (@fixture_so) { print "XS $_\n"; }
my $temp=tempdir(CLEANUP=>1);
my $db=DBI->connect("dbi:SQLite:dbname=$temp/main.db","","",{RaiseError=>1,PrintError=>0});
my ($sqlite)=$db->selectrow_array('SELECT sqlite_version()');
print "SQLite $sqlite\n";
my ($wal)=$db->selectrow_array('PRAGMA journal_mode=WAL');
die 'WAL' unless $wal eq 'wal';
$db->do('CREATE TABLE result (n INTEGER)');
$db->begin_work;
$db->do('INSERT INTO result VALUES (40)');
$db->commit;
$db->begin_work;
$db->do('INSERT INTO result VALUES (99)');
$db->rollback;
die 'transaction' unless ($db->selectrow_array('SELECT sum(n) FROM result'))[0]==40;
$db->sqlite_backup_to_file("$temp/backup.db");
my $backup=DBI->connect("dbi:SQLite:dbname=$temp/backup.db","","",{RaiseError=>1,PrintError=>0});
die 'backup' unless ($backup->selectrow_array('SELECT n FROM result'))[0]==40;
$backup->disconnect; $db->disconnect;
my $audio=Audio::Scan->scan($wav);
die 'audio' unless $audio->{info}{samplerate}==44100 && $audio->{info}{channels}==2;
my $image=Image::Scale->new($png) or die 'image';
$image->resize_gd({width=>1,height=>1});
$image->save_png("$temp/resize.png");
die 'image result' unless -s "$temp/resize.png";
die 'JSON' unless JSON::XS::decode_json(JSON::XS::encode_json({n=>42}))->{n}==42;
die 'YAML' unless YAML::XS::Load(YAML::XS::Dump({n=>42}))->{n}==42;
my $done=0;
my $timer=EV::timer(0,0,sub {$done=1; EV::break()});
EV::run();
die 'EV' unless $done;
my $blessed=bless {answer=>42},'FixtureProbe';
Class::XSAccessor->import(class=>'FixtureProbe',getters=>{answer=>'answer'});
die 'XS accessor' unless $blessed->answer==42;
print "PASS functions\n";
if (defined $ENV{EXPECTED_LIBPERL}) {
 open my $maps, '<', '/proc/self/maps' or die $!;
 my %paths;
 while (<$maps>) {
  if (m{\s(/\S*/libperl\.so\.\S+)\s*$}) { $paths{$1}=1; }
 }
 die "Wrong libperl mappings: ".join(" ",keys %paths)
   unless keys(%paths)==1 && $paths{$ENV{EXPECTED_LIBPERL}};
 print "MAP $ENV{EXPECTED_LIBPERL}\n";
}
