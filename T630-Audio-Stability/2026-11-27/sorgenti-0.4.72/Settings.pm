package Plugins::DaphileCamillaDSP::Settings;
use strict;
use warnings;
use base qw(Slim::Web::Settings);

sub name { Slim::Web::HTTP::CSRF->protectName('PLUGIN_DAPHILECAMILLADSP') }
sub page { Slim::Web::HTTP::CSRF->protectURI('plugins/DaphileCamillaDSP/settings-0.4.41.html') }
sub beforeRender {
    my ($class, $params) = @_;
    $params->{native_panel_present} = -f '/etc/daphile-custom-release'
        && -f '/var/www/localhost/cgi-bin/CamillaPlugin';
}
1;
