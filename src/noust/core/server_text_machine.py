# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The sentences of rest that the server says for the console to translate (item 151).

A table of :mod:`noust.core.server_text`, which merges it into its catalog: one sentence is
its entry here and a code to ask for it, and the console's own catalogs say it in Spanish. Kept
apart so the catalog does not grow past the size a module may have."""

from __future__ import annotations

SENTENCES: dict[str, str] = {
    "collect.ceiling": "{node} does not let this central collect its copies",
    "collect.ceiling.hint": (
        "Its access level for a central is below admin, and a copy carries away the .env and the "
        "databases of every application, which is more than a deploy level promises. On {node}, as "
        "root, run 'noust fleet access --level admin' to allow it, or leave the copies where they "
        "are."
    ),
    "config.deploy_here_not_from_console": (
        "Deploying on this machine's Docker is not turned on from the console"
    ),
    "config.deploy_here_not_from_console.hint": (
        "It gives this central the power of root on the machine, so it is asked for where the "
        "compose file that mounts Docker's socket is written: add NOUST_DEPLOY_HERE=containers to "
        "its environment (see the guide to deploying where the central runs), then recreate the "
        "container."
    ),
    "http3.nginx_refused_site_holds": "nginx refused the site that holds the QUIC socket",
    "http3.nginx_refused_site_holds.hint": "{errors}",
    "log_retention.days_number": "days is a number from {min_days} to {max_days}",
    "log_retention.most_disk_number_megabytes": "The most disk is a number of megabytes from {min_mb} to {max_mb}",
    "log_retention.name_keep_days_max": "Name what to keep: --days, --max-use or --persistent",
    "log_retention.read": "{dropin} could not be read",
    "log_retention.read.hint": "{exc}",
    "log_retention.retention_written_journald_restarted": "The retention was written but journald could not be restarted",
    "log_retention.retention_written_journald_restarted.hint": "{value}",
    "log_retention.written_noust": "{dropin} was not written by Noust",
    "log_retention.written_noust.hint": "Move it aside, or set the retention in that file yourself.",
    "log_retention.written_noust_2": "{dropin} was not written by Noust",
    "log_retention.written_noust_2.hint": "It is left as it is.",
    "log_search.level": "{level} is not a level",
    "log_search.level.hint": "The levels are: {items}.",
    "log_search.limit_number_1": "limit is a number from 1 to {max_limit}",
    "log_search.time": "{text} is not a time",
    "log_search.time.hint": "Use epoch seconds, a time ago such as 15m, 2h or 7d, or a date such as 2026-10-16.",
    "log_search.window_ends_before_starts": "The window ends before it starts",
    "log_search.window_longer_journal_hold": "The window is longer than the journal can hold",
    "log_search.window_longer_journal_hold.hint": "Ask for at most {value} days at a time.",
    "storage.docker_image_rm_failed": "'docker image rm {ref}' failed",
    "storage.docker_image_rm_failed.hint": "Nothing else was changed. The tool's own output is below.",
    "storage.docker_installed_system": "Docker is not installed on this system",
    "storage.failed_exit_code": "'{items}' failed (exit code {exit_code})",
    "storage.failed_exit_code.hint": "Nothing else was changed. The tool's own output is below.",
    "storage.give_size_age_both": "Give a size or an age, not both",
    "storage.give_size_age_both.hint": "Use size_mb or days.",
    "storage.image_list_unused_images": "That image is not in the list of unused images",
    "storage.image_list_unused_images.hint": "It may be in use by a container, or not be there any more.",
    "storage.image_list_unused_images_2": "That image is not in the list of unused images: it is a version {value} to go back to",
    "storage.image_list_unused_images_2.hint": "Noust never offers it for removal. An application keeps fewer versions with: noust releases keep",
    "storage.name_image_id": "Name the image by its id",
    "storage.name_image_id.hint": "Use an id from the list of unused images.",
    "storage.package_cache_clean_system": "There is no package cache to clean on this system",
    "storage.package_cache_clean_system.hint": "{value}",
    "storage.positive_whole_number": "{name} must be a positive whole number",
    "storage.positive_whole_number.hint": "Got {value}.",
    "storage.psutil_installed_disks_listed": "psutil is not installed, so disks cannot be listed",
    "storage.psutil_installed_disks_listed.hint": "Install the web extra: pip install 'noust[web]'.",
    "storage.unknown_cleanup_action": "Unknown cleanup action: {action}",
    "storage.unknown_cleanup_action.hint": "Use one of: {items}.",
    "waf.nginx_refused_waf_rules": "nginx refused the WAF's rules",
    "waf.nginx_refused_waf_rules.hint": "{errors}",
    "waf.noust_installs_waf_debian": "Noust installs the WAF on Debian and Ubuntu only",
    "waf.noust_installs_waf_debian.hint": "Elsewhere install nginx's ModSecurity module and the OWASP Core Rule Set from your distribution or from nginx.org, then turn the WAF on.",
    "waf.packages_installed": "The packages could not be installed",
    "waf.packages_installed.hint": "The package manager's output is below.",
    "waf.waf_packages_complete": "The WAF's packages are not complete",
    "waf.waf_packages_complete.hint": "Reinstall libnginx-mod-http-modsecurity and modsecurity-crs.",
    "http3.apache": "Apache's stock builds have no HTTP/3",
    "http3.apache.hint": "Serve this site with nginx 1.25 or later to use HTTP/3.",
    "http3.no_version": "nginx's version could not be read",
    "http3.no_version.hint": "Run: {command}",
    "http3.not_built": "This nginx {version} was built without --with-http_v3_module",
    "http3.not_built.hint": "Install the build from nginx.org, which includes it.",
    "http3.too_old": "nginx {version} is older than 1.25.0, the first with HTTP/3",
    "http3.too_old.hint": "Install a newer nginx: the nginx.org packages (https://nginx.org/en/linux_packages.html) carry it on Debian, Ubuntu and RPM systems.",
    "storage.cleanup_deletes": "'{action}' deletes something you may want back",
    "storage.cleanup_deletes.hint": "{effect}",
    "waf.base_missing": "ModSecurity's base settings or the Core Rule Set's setup file are missing",
    "waf.base_missing.hint": "Reinstall the packages: libnginx-mod-http-modsecurity and modsecurity-crs.",
    "waf.module_missing": "nginx has no ModSecurity module",
    "waf.module_missing.hint": "Install it: {command} (Debian and Ubuntu: libnginx-mod-http-modsecurity and modsecurity-crs).",
    "waf.module_not_loaded": "The ModSecurity module is installed but nginx does not load it",
    "waf.module_not_loaded.hint": "Load it with a load_module line in nginx.conf (Debian and Ubuntu: {command}).",
    "waf.not_nginx": "The WAF is nginx's here: Noust drives ModSecurity through nginx",
    "waf.not_nginx.hint": "Serve this site with nginx to use the WAF.",
    "waf.rules_missing": "The OWASP Core Rule Set is not installed",
    "waf.rules_missing.hint": "Install it: {command} (Debian and Ubuntu: modsecurity-crs).",
    "storage.backups_summary": "{count} backups; their retention is a backup setting",
    "storage.reduce_to": "Reduce it to {megabytes} MB",
    "storage.releases_over_keep": "{apps} application(s) keep more releases than they are set to",
    "log_retention.dropin_foreign": "{dropin} exists and Noust did not write it, so it is left alone.",
    "log_retention.journal_volatile": "The journal is kept in memory and lost at every reboot. Add --persistent to keep it.",
    "log_search.container_unreadable": "{domain}: {service} could not be read ({reason})",
    "log_search.containers_unlisted": "{domain}: its containers could not be listed ({reason})",
    "log_search.journal_failed": "The journal could not be searched: {reason}",
    "log_search.no_app_matches": "No application you can search matches {names}",
    "log_search.stacks_no_levels": "The containers of Docker stacks have no levels, so they are not searched when the query names a level.",
}
