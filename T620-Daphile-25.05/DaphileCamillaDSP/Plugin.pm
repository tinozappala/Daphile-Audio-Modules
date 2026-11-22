package Plugins::DaphileCamillaDSP::Plugin;
use strict;
use warnings;
use base qw(Slim::Plugin::Base);

sub initPlugin {
    my $class = shift;
    $class->SUPER::initPlugin(@_);
    if (main::WEBUI) {
        require Plugins::DaphileCamillaDSP::Settings;
        Plugins::DaphileCamillaDSP::Settings->new;
    }
}

sub getDisplayName { 'PLUGIN_DAPHILECAMILLADSP' }

# Enabling the page must not activate DSP or change LMS transcoding rules.
1;
