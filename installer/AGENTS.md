# OpenWrt Tools development

- This repository contains the common installer, desktop/mobile UI, adapters, Diff Studio, release index and OTA runtime. It contains no device kernel/rootfs or user NV.
- Preserve the E5 and MU300 adapter boundaries. E5 working-NV preservation and typed original-NV recovery must never run on MU300. Both families support eMMC/UFS; keep their existing identity algorithms and saved-record semantics.
- Do not replace one family's code with the other family's implementation to satisfy a missing input. Select family from the validated package. No fastboot; retain dd + binary ADB transfers and current boot-slot handling.
- Hardware access is read-only unless the user authorizes the particular deployment/acceptance action. Do not terminate or reinstall a control App running an installation. Offline tests are not hardware validation.
- Runtime assets are versioned under runtime/. Builds must not read a neighbouring firmware tree to obtain a missing helper. Optional firmware-source integration tests use OPENWRT_E5_SOURCE / OPENWRT_MU300_SOURCE explicitly.
- Read docs/BUILD.zh-CN.md before a build. Reuse this machine's existing native SDK/JDK/Rust/Python caches and /home/kano/openwrt-build. Do not clone kernels, duplicate rootfs, migrate to C:, or create timestamped build trees.
- Use the version-controlled tools/build-release.py run entry for standalone builds, or the parent firmware's existing build-release.py run when this repository is used as a submodule. Logs/status stay under out/. No ad-hoc release workers.
- Private signing keys/configuration remain outside Git. Never include them, local.properties, device logs, private installation records, or cache directories in a commit or release.
- E5 and MU300 consume this repository as an installer/ Git submodule pinned to a reviewed commit. Do not mirror full source directories or update to an unpinned branch during packaging.
