# UAV Windows FFmpeg build

The release supplies a separate FFmpeg 7.1 executable under LGPL-2.1-or-later.
It is built from the exact official source/hash in `source.json`, without
source patches, GPL/version3/nonfree components, external auto-detected codec
libraries, network protocols, hardware acceleration, or video encoders. Native
demuxers/decoders/parsers/filters/muxers remain available; PCM audio encoding is
enabled for subtitle extraction. UAV's own license is unchanged.

On Ubuntu 24.04, install `python3`, `make`, `gcc-mingw-w64-x86-64-win32`,
`binutils-mingw-w64-x86-64`, and `xz-utils` from the distribution repositories.
Then run:

```sh
python3 packaging/ffmpeg/build.py --work-dir /tmp/uav-ffmpeg-build --out-dir /tmp/uav-ffmpeg-bundle
```

Both directories must be empty; the script does not recursively delete them.
The corresponding-source release archive includes the exact official source
tarball, this recipe, flags, tool/package versions and notices. To rebuild from
that archive offline, extract it, install the build tools, and run its `build.py`
with `--source-archive ffmpeg-7.1.tar.xz` and new empty work/output directories.
Compiler/system components are identified in the package inventory. No external
codec libraries or hidden source patches are used. Byte-for-byte reproducibility
across different compiler/package versions is not promised.

Windows CI verifies the binary's license/configuration, system DLL imports,
Unicode paths, H.264/HEVC remux and common-container audio extraction before
packaging. Set `UAV_FFMPEG_BUNDLE` to that validated bundle when running the
Windows PyInstaller specs. The original imageio FFmpeg binary is stripped both
before and after Analysis. Missing/mismatched binary or source provenance fails
the build; there is no silent fallback to imageio's upstream GPL binary.

Publish `UAV_FFmpeg_7.1_Corresponding_Source.tar.xz` next to the Windows assets
with checksums and provenance. It is not a runtime download for users. The
application can use a user-supplied compatible FFmpeg companion/PATH executable;
no restriction is added against replacing or modifying this separate component.
This archive does not provide corresponding source for older Gyan-based UAV
releases.
