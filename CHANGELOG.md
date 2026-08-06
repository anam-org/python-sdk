# Changelog

All notable changes to the anam Python SDK will be documented in this file.

This project adheres to [Semantic Versioning](https://semver.org/) and uses [Conventional Commits](https://www.conventionalcommits.org/) for automated releases.

<!-- version list -->

## v0.9.0-alpha.4 (2026-08-06)

### Features

- Session region controls ([#66](https://github.com/anam-org/python-sdk/pull/66),
  [`7d75bfc`](https://github.com/anam-org/python-sdk/commit/7d75bfca10f206d79e68c89d2f6a2196e08d50b8))


## v0.9.0-alpha.3 (2026-08-05)

### Features

- Support additional session request headers ([#69](https://github.com/anam-org/python-sdk/pull/69),
  [`a6d5947`](https://github.com/anam-org/python-sdk/commit/a6d594765a03e4367ef9c575db500929073bd163))


## v0.9.0-alpha.2 (2026-08-05)

### Bug Fixes

- Make auto-approval workflow bootstrap-safe ([#68](https://github.com/anam-org/python-sdk/pull/68),
  [`b6d7eb6`](https://github.com/anam-org/python-sdk/commit/b6d7eb67146905665405ea1574a05a82c8ebd32b))

- Retain API-key persona validation ([#68](https://github.com/anam-org/python-sdk/pull/68),
  [`b6d7eb6`](https://github.com/anam-org/python-sdk/commit/b6d7eb67146905665405ea1574a05a82c8ebd32b))

- Support token-authenticated messages ([#68](https://github.com/anam-org/python-sdk/pull/68),
  [`b6d7eb6`](https://github.com/anam-org/python-sdk/commit/b6d7eb67146905665405ea1574a05a82c8ebd32b))

### Continuous Integration

- Auto-approve low-risk and hot-fix PRs ([#68](https://github.com/anam-org/python-sdk/pull/68),
  [`b6d7eb6`](https://github.com/anam-org/python-sdk/commit/b6d7eb67146905665405ea1574a05a82c8ebd32b))

### Features

- Support pre-minted session tokens ([#68](https://github.com/anam-org/python-sdk/pull/68),
  [`b6d7eb6`](https://github.com/anam-org/python-sdk/commit/b6d7eb67146905665405ea1574a05a82c8ebd32b))


## v0.9.0-alpha.1 (2026-08-05)

### Features

- Forward engine routing overrides via ClientOptions.environment (ENG-2612)
  ([`fb073ad`](https://github.com/anam-org/python-sdk/commit/fb073ad29199c32dfadbd422b5a8ed0f2cfd7bd5))


## v0.8.0 (2026-08-03)


## v0.7.0-alpha.2 (2026-07-21)

### Documentation

- Clarify avatar video frame rate
  ([`a1d7553`](https://github.com/anam-org/python-sdk/commit/a1d75534af5f7c2b563b959c345a1469014cc634))

### Features

- Add widht/height to sessionOptions for Cara-4 portrait
  ([`89f6d92`](https://github.com/anam-org/python-sdk/commit/89f6d922a32ee744997022e984e2dd424103370b))


## v0.7.0-alpha.1 (2026-07-08)

### Documentation

- Default examples to use Cara-4
  ([`a03f4b1`](https://github.com/anam-org/python-sdk/commit/a03f4b1c19d3da7d8d7e832146493fb78d1d00e2))

### Features

- Director notes API
  ([`e49ef36`](https://github.com/anam-org/python-sdk/commit/e49ef3640448a9994e89aebeb2ee770e3e392e10))


## v0.6.0 (2026-06-17)


## v0.5.0-alpha.2 (2026-06-17)

### Features

- Start sessions via direct API-key route
  ([`ea783e0`](https://github.com/anam-org/python-sdk/commit/ea783e0e15da20abcd8f783439220c0bb5a9a077))


## v0.5.0-alpha.1 (2026-06-02)

### Documentation

- Add direct egress description and warning for the experimental nature of the feature
  ([`01a58bb`](https://github.com/anam-org/python-sdk/commit/01a58bba0c3addf8bcbef93ef455690048a91791))

### Features

- Expand sessionOptions with egressOptions
  ([`01a58bb`](https://github.com/anam-org/python-sdk/commit/01a58bba0c3addf8bcbef93ef455690048a91791))

- Introducing direct egress support for 3rd party real-time network delivery via
  sessionOptions.egress; add support for Daily rooms
  ([`01a58bb`](https://github.com/anam-org/python-sdk/commit/01a58bba0c3addf8bcbef93ef455690048a91791))


## v0.4.0 (2026-04-16)


## v0.4.0-alpha.3 (2026-04-16)

### Features

- Pin high video quality ([#53](https://github.com/anam-org/python-sdk/pull/53),
  [`4b14292`](https://github.com/anam-org/python-sdk/commit/4b142928143550de7fcfc8fc0ef3e24397abf80f))


## v0.4.0-alpha.2 (2026-03-20)

### Features

- Add USER_SPEECH_STARTED and USER_SPEECH_ENDED events
  ([#46](https://github.com/anam-org/python-sdk/pull/46),
  [`11713ed`](https://github.com/anam-org/python-sdk/commit/11713ed8f2839ba29e2df97ad99902d7517cafd1))


## v0.4.0-alpha.1 (2026-02-26)

### Features

- Add TalkMessageStream support
  ([`b93623a`](https://github.com/anam-org/python-sdk/commit/b93623aff48d15bcb3dea71c4c36813e5b0c8aa3))


## v0.3.0 (2026-02-18)


## v0.3.0-alpha.1 (2026-02-18)

### Features

- Support session options
  ([`530ca09`](https://github.com/anam-org/python-sdk/commit/530ca09b3c8004fa1135eee08e1efd87ed9ed6ba))


## v0.2.0 (2026-02-12)

### Chores

- **deps**: Bump cryptography from 46.0.3 to 46.0.5
  ([`9bc6ba8`](https://github.com/anam-org/python-sdk/commit/9bc6ba878ad5c7e773b0f24d716f14f66268add6))


## v0.2.0-alpha.2 (2026-02-10)

### Bug Fixes

- Expose send_user_audio publicly
  ([`308b50e`](https://github.com/anam-org/python-sdk/commit/308b50e321a27c0a065a11c41951717297302cb2))


## v0.2.0-alpha.1 (2026-02-06)

### Chores

- Clean session end handling
  ([`ff99c57`](https://github.com/anam-org/python-sdk/commit/ff99c57d3930319c1acabe778723948f233ebf38))

### Documentation

- Change anam-ai to anam
  ([`2b725fc`](https://github.com/anam-org/python-sdk/commit/2b725fc4835d7180c8139828c267f31b740c702c))

### Features

- Support user audio input
  ([`be8f8bd`](https://github.com/anam-org/python-sdk/commit/be8f8bd897593659498ea497321c3611702a7246))


## v0.1.0 (2026-01-27)


## v0.0.1 (2026-01-27)


## v0.0.1-alpha.1 (2026-01-27)

- Initial Release
