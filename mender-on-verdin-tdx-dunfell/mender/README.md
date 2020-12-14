This directory contains `mender-binary-delta`. It is used to enable delta updates for mender.
Please follow the [mender docs](https://docs.mender.io/system-updates-yocto-project/customize-mender/delta-update-support#download-mender-binary-delta) on how to install `mender-binary-delta`.

After installation this directory shall contain the following tree:

```
$ pwd
/<path-to-project>/ci.os.lmp/mender-on-verdin-tdx-dunfell/mender
$ tree .
.
├── mender-binary-delta
│   ├── aarch64
│   │   ├── mender-binary-delta
│   │   └── mender-binary-delta-generator
│   ├── arm
│   │   ├── mender-binary-delta
│   │   └── mender-binary-delta-generator
│   └── x86_64
│       ├── mender-binary-delta
│       └── mender-binary-delta-generator
```
