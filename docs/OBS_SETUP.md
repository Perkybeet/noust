# OBS (Open Build Service) Setup Guide

This guide explains how to build and distribute Noust packages via the Open Build Service, allowing you to support multiple Linux distributions (Fedora, openSUSE, Debian, Ubuntu, Arch, etc.) from a single source.

## Why OBS?

- **Multi-distribution**: Build for Ubuntu, Debian, Fedora, openSUSE, Arch, Mageia, etc. from a single source
- **Multi-format**: Generate .deb, .rpm, and other package formats automatically
- **Automatic builds**: Git webhooks can trigger rebuilds automatically
- **Free hosting**: Repository hosting included at build.opensuse.org
- **CI/CD ready**: Built-in continuous integration

## Quick Start

### 1. Register Account

Go to **https://build.opensuse.org** and:
- Click "Sign Up"
- Use GitHub/Google account or create new one
- Verify email

### 2. Create Your Project

In OBS web interface:
1. Login → **"Home Project"** (auto-created as `home:your_username`)
2. Click **"Create package"**
3. Package name: `noust`. A second package in the same project, `wasm`, builds the empty
   transitional packages (`wasm` for Debian and Ubuntu, `wasm-cli` for Fedora and openSUSE)
   that pull in `noust`, so a server that has either upgrades onto it. See
   [RENAME.md](RENAME.md).
4. (Optional) Add description

### 3. Install OSC (OBS Command Line)

```bash
# Ubuntu/Debian
sudo apt install osc

# Fedora
sudo dnf install osc

# Or via pip
pip install osc
```

### 4. Configure OSC

```bash
# Set API URL
osc config set apiurl https://api.opensuse.org

# Authenticate (will prompt for username/password)
osc ls
```

This creates `~/.oscrc` with your credentials.

### 5. Choose Target Distributions

In OBS web interface (your package page):
1. Go to **"Repositories"** tab
2. Click **"Add from a Distribution"**
3. Select distributions you want to support:
   - Ubuntu 24.04 (Noble Numbat)
   - Debian 12 (Bookworm)
   - Fedora 40, 41
   - openSUSE Tumbleweed, Leap 15.6
   - Arch Linux

Or add manually in project config (`osc meta prj -e home:your_username`):

```xml
<repository name="Ubuntu_24.04">
  <path project="Ubuntu:24.04" repository="universe"/>
  <arch>x86_64</arch>
</repository>
<repository name="Fedora_40">
  <path project="Fedora:40" repository="standard"/>
  <arch>x86_64</arch>
</repository>
```

## Building and Uploading

### Automatic (Recommended)

Use the provided script:

```bash
# Make executable
chmod +x build-and-upload-obs.sh

# Upload with defaults (home:Perkybeet/noust)
./build-and-upload-obs.sh

# Or specify custom project/package
./build-and-upload-obs.sh home:myuser noust
```

The script will:
1. Create source tarball from Git
2. Checkout OBS package
3. Copy `.spec` file and tarball
4. Update version
5. Commit to OBS
6. OBS automatically starts building for all configured distributions

### Manual Process

```bash
# 1. Create tarball
VERSION=$(head -n 1 debian/changelog | sed 's/.*(\(.*\)).*/\1/' | cut -d'~' -f1)
git archive --format=tar.gz --prefix=noust-${VERSION}/ HEAD > noust-${VERSION}.tar.gz

# 2. Checkout package
osc checkout home:Perkybeet/noust
cd home:Perkybeet/noust

# 3. Copy files
cp /path/to/noust-${VERSION}.tar.gz .
cp /path/to/rpm/noust.spec .
cp /path/to/obs/_service .

# 4. Add and commit
osc add noust-${VERSION}.tar.gz noust.spec _service
osc commit -m "Update to version ${VERSION}"
```

### Using Makefile

Add to your workflow:

```bash
# Build and upload to OBS
make obs-upload

# Just build locally (test)
make obs-build

# Check build status
make obs-status
```

## Monitoring Builds

### Web Interface

View build status at:
```
https://build.opensuse.org/package/show/home:Perkybeet/noust
```

### Command Line

```bash
# Check build results
osc results home:Perkybeet noust

# Watch live (updates every 10 seconds)
watch -n 10 "osc results home:Perkybeet noust"

# Get build logs for specific distribution
osc buildlog home:Perkybeet noust Fedora_40 x86_64

# Download built packages
osc getbinaries home:Perkybeet noust Fedora_40 x86_64
```

## Repository Setup for Users

Once packages are built, users can install from OBS repositories:

### Fedora

```bash
# Add repository
sudo dnf config-manager --add-repo \
  https://download.opensuse.org/repositories/home:/Perkybeet/Fedora_40/home:Perkybeet.repo

# Install
sudo dnf install noust
```

### openSUSE

```bash
# Add repository
sudo zypper ar -f \
  https://download.opensuse.org/repositories/home:/Perkybeet/openSUSE_Tumbleweed/ \
  home_Perkybeet

# Install
sudo zypper install noust
```

### Ubuntu/Debian

```bash
# Add repository key
curl -fsSL https://download.opensuse.org/repositories/home:/Perkybeet/xUbuntu_24.04/Release.key | \
  gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/home_Perkybeet.gpg > /dev/null

# Add repository
echo 'deb https://download.opensuse.org/repositories/home:/Perkybeet/xUbuntu_24.04/ /' | \
  sudo tee /etc/apt/sources.list.d/home_Perkybeet.list

# Install
sudo apt update
sudo apt install noust
```

### Arch Linux (via AUR helper or manual)

OBS provides binary packages, but you may want to also publish on AUR.

## Automated Git Integration

### Method 1: Service File (Recommended)

The `obs/_service` file enables automatic source fetching:

```xml
<services>
  <service name="obs_scm">
    <param name="url">https://github.com/Perkybeet/noust.git</param>
    <param name="scm">git</param>
    <param name="revision">main</param>
  </service>
  <service name="tar" mode="buildtime"/>
  <service name="recompress" mode="buildtime">
    <param name="compression">gz</param>
  </service>
</services>
```

When you commit, OBS will automatically fetch from GitHub.

### Method 2: Webhook (Fully Automated)

Set up GitHub webhook to trigger OBS builds:

1. In OBS: Go to package → **"Trigger"** → Get webhook URL
2. In GitHub: `Settings` → `Webhooks` → **"Add webhook"**
3. Paste OBS webhook URL
4. Select events: `Push` and `Release`
5. Save

Now every push to `main` automatically triggers OBS builds!

## File Structure

```
noust/
├── rpm/
│   └── noust.spec              # RPM package specification
├── obs/
│   ├── _service                # OBS service configuration (optional)
│   ├── debian.changelog        # Debian changelog
│   ├── debian.control          # Debian package metadata
│   ├── debian.rules            # Debian build rules
│   ├── debian.postinst         # Post-install script
│   ├── debian.postrm           # Post-remove script
│   ├── debian.copyright        # License info
│   ├── noust.dsc               # Debian source control
│   ├── noust.1                 # Man page
│   ├── noust.default.yaml      # Default config
│   ├── noust.dirs              # Directories to create
│   ├── noust.manpages          # Man pages list
│   └── source/
│       ├── format              # Source format
│       └── options             # Build options
└── build-and-upload-obs.sh     # Upload to OBS
```

## Differences: .deb vs .rpm Packaging

| Aspect | Debian (.deb) | RPM (.rpm) |
|--------|---------------|------------|
| Control file | `debian/control` | `.spec` file |
| Build rules | `debian/rules` | `%build` section in .spec |
| Dependencies | `Depends:` | `Requires:` |
| Build deps | `Build-Depends:` | `BuildRequires:` |
| Install script | `debian/postinst` | `%post` in .spec |
| File list | `debian/*.install` | `%files` in .spec |

## Troubleshooting

### Build Failures

```bash
# View build log
osc buildlog home:Perkybeet noust Fedora_40 x86_64

# Common issues:
# - Missing BuildRequires in .spec
# - Python dependencies not available
# - File permissions issues
```

### Authentication Issues

```bash
# Reconfigure OSC
rm ~/.oscrc
osc config set apiurl https://api.opensuse.org
osc ls  # Will prompt for credentials
```

### Package Not Found

Ensure:
1. Package created in OBS web interface
2. Repositories configured (Fedora_40, Ubuntu_24.04, etc.)
3. Project and package names match in script

### Dependencies Not Available

For Python packages not in distribution repos, you may need to:
1. Build them in separate OBS package
2. Add to `BuildRequires` from PyPI during build
3. Use bundled dependencies (not recommended)

## Best Practices

1. **Version Tags**: Use Git tags (`v0.9.1`) for releases, OBS can auto-detect
2. **Test Locally**: Use `osc build` to test before uploading
3. **Watch First Build**: Monitor first build of each distro carefully
4. **Update Changelog**: Keep `.spec` and `debian.changelog` updated
5. **Sync Versions**: Keep version numbers consistent across all package files

## Workflow Summary

| Task | Command |
|------|---------|
| Upload to OBS | `./build-and-upload-obs.sh` or `make obs-upload` |
| Check status | `make obs-status` or `osc results home:Perkybeet noust` |
| View logs | `make obs-logs DISTRO=Fedora_40 ARCH=x86_64` |
| Watch builds | `make obs-status-watch` |

## Next Steps

1. Register at build.opensuse.org
2. Install and configure `osc`
3. Create package in OBS web UI
4. Add target distributions
5. Run `./build-and-upload-obs.sh`
6. Monitor builds
7. Update README with install instructions

## Resources

- **OBS Portal**: https://build.opensuse.org
- **Documentation**: https://openbuildservice.org/help/manuals/obs-user-guide/
- **Package Guidelines**: https://en.opensuse.org/openSUSE:Packaging_guidelines
- **RPM Packaging Guide**: https://rpm-packaging-guide.github.io/
- **OSC Cheat Sheet**: https://en.opensuse.org/openSUSE:OSC

## Support

For issues with OBS builds, check:
- Build logs: `osc buildlog home:Perkybeet noust <distro> <arch>`
- OBS forums: https://forums.opensuse.org/
- Matrix: #opensuse-buildservice:opensuse.org
