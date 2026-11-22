package Plugins::DaphileRME::Plugin;
use strict;
use warnings;
use base qw(Slim::Plugin::Base);

sub initPlugin {
    my $class = shift;
    $class->SUPER::initPlugin(@_);
    if (main::WEBUI) {
        require Plugins::DaphileRME::Settings;
        Plugins::DaphileRME::Settings->new;
    }
}

sub getDisplayName { 'PLUGIN_DAPHILERME' }

# Frontend only. No MIDI write, player restart, process or global preference.
1;
