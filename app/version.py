# This fork versions itself independently of the PasarGuard release it is built
# on. UPSTREAM_VERSION records that release, so anything that needs to know
# which upstream code is running still has the real number.
#
# Changing __version__ on main publishes a GitHub Release (.github/workflows/
# release.yml), which is what makes every running panel offer the update - the
# banner reads the Releases API, so a version that is never released is a
# version nobody is told about.
__version__ = "0.1.4"
UPSTREAM_VERSION = "5.3.0"
