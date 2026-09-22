# How the compile checks were run (rerunnable)

Box: Debian 12, 4 CPUs, 3.9 GB RAM, gcc 12. No Ardour was built or executed.

## Dependencies installed (apt)
libglibmm-2.4-dev liblo-dev vamp-plugin-sdk librubberband-dev liblua5.3-dev
libboost-dev libxml2-dev libsigc++-2.0-dev libarchive-dev libcurl4-openssl-dev
libsndfile1-dev libsamplerate0-dev libaubio-dev libtag1-dev libfftw3-dev
lv2-dev liblilv-dev libsuil-dev libserd-dev libsord-dev libsratom-dev
libfluidsynth-dev

## Two waf-generated headers had to be supplied
- `libardour-config.h` — a minimal empty stand-in (waf normally generates it
  from the configure step). Nothing in this patch depends on its contents.
- `pbd/signals_generated.h` (8.12 only) — generated properly by running
  Ardour's own generator: `python3 libs/pbd/pbd/signals.py <out>`

## The commands
    PKGS="glibmm-2.4 libxml-2.0 liblo sndfile samplerate vamp-hostsdk \
          rubberband libarchive libcurl"
    CF=$(pkg-config --cflags $PKGS)
    INC="-I<gen> -Ilibs/ardour -Ilibs/pbd -Ilibs/temporal -Ilibs/evoral \
         -Ilibs/midi++2 -Ilibs/lua -Ilibs/ptformat -Ilibs/audiographer \
         -Ilibs/zita-resampler -Ilibs/zita-convolver -Ilibs/fluidsynth \
         -Ilibs/qm-dsp -Ilibs/libltc/ltc -Ilibs/libltc -Ilibs/vamp-plugins \
         -Ilibs/aaf -Ilibs/vst3 -Ilibs/ctrl-interface/control_protocol \
         -Ilibs/gtkmm2ext -Ilibs/widgets -Ilibs/canvas -Ilibs/waveview"
    DEF='-DPACKAGE="ardour" -DLOCALEDIR="/usr/share/locale" \
         -DVERSIONSTRING="9.8" -DPROGRAM_NAME="Ardour" -DLUA_USE_POSIX -DWAF_BUILD'

    g++ -std=c++17 -fsyntax-only $DEF $INC $CF libs/ardour/lua_api.cc
    g++ -std=c++17 -fsyntax-only $DEF $INC $CF libs/ardour/luabindings.cc

## Results
| tree | lua_api.cc | luabindings.cc |
|---|---|---|
| ardour 9.8  | exit 0, no diagnostics | exit 0, no diagnostics |
| ardour 8.12 | exit 0, no diagnostics | exit 0, one unrelated Boost deprecation pragma |

## Patch application
Each patch was checked against a checkout first confirmed pristine by
`git status --porcelain` (0 lines), then `git apply --check` — both OK.
