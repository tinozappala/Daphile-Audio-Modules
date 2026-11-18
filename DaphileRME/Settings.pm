package Plugins::DaphileRME::Settings;
use strict;
use warnings;
use base qw(Slim::Web::Settings);

sub name { Slim::Web::HTTP::CSRF->protectName('PLUGIN_DAPHILERME') }
sub page { Slim::Web::HTTP::CSRF->protectURI('plugins/DaphileRME/settings-0.5.7.html') }
sub beforeRender {
    my ($class, $params) = @_;
    # Presence of the existing page is not a claim of DAC/service readiness.
    $params->{native_panel_present} = -f '/etc/daphile-custom-release'
        && -f '/var/www/localhost/cgi-bin/RMEPlugin';
}
1;
