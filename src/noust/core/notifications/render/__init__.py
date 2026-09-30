# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""One pure renderer per channel: a notification in, what that channel receives out."""

from noust.core.notifications.render import discord, email, slack, telegram, webhook

__all__ = ["discord", "email", "slack", "telegram", "webhook"]
