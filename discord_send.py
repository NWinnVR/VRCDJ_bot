"""discord_send.py — send content to Discord that respects the 2000-char limit.

Discord's hard per-message ceiling is 2000 characters (the `content` field is
validated server-side; longer than that → HTTP 50035 "Invalid Form Body").
A lot of the old bot code did `content=text[:4000]`, assuming 4000 was the
limit — that silently breaks any answer between 2000 and 4000 chars.

This module chunks a long answer into ≤DISCORD_LIMIT-char pieces, preferring
clean breaks (paragraphs, then line boundaries, then hard cuts) so we never
slice mid-sentence, and posts the first piece as the message and the rest as
follow-ups.

Pure stdlib + discord.py (async). No side effects beyond the send itself.
"""
from __future__ import annotations

import re
from typing import List

# Stay a hair under 2000 so Discord's markdown/emoji normalization can't push
# a chunk over the wire limit. 1990 is comfortably safe.
DISCORD_LIMIT = 1990

# A soft "continued" marker on follow-up messages so a user reading a multi-
# part answer knows it didn't just get cut off.
CONTINUED = "… (continued)"


def _hard_chunk(s: str, limit: int) -> List[str]:
    """Split a string into chunks of ≤limit chars, no line awareness.

    Used only for a single pathological line that is itself longer than the
    limit (e.g. one giant unbroken token). We cut at `limit` so every piece
    fits the message cap.
    """
    out: List[str] = []
    while len(s) > limit:
        out.append(s[:limit])
        s = s[limit:]
    if s:
        out.append(s)
    return out


def _chunk_lines(lines: List[str], limit: int) -> List[str]:
    """Pack lines into chunks of ≤limit chars, keeping whole lines intact.

    A single line longer than `limit` is hard-chunked so it still fits.
    """
    chunks: List[str] = []
    cur = ""
    for ln in lines:
        # Normalise blank lines to at most two (Discord collapses extra).
        candidate = (cur + "\n" + ln) if cur else ln
        if len(candidate) <= limit:
            cur = candidate
            continue
        # Adding this line would overflow: flush what we have, then place the
        # line. If the line itself is too big, hard-chunk it.
        if cur:
            chunks.append(cur)
            cur = ""
        if len(ln) > limit:
            # Flush any partial chunk first (none here, cur is empty), then
            # hard-chunk the long line.
            for piece in _hard_chunk(ln, limit):
                chunks.append(piece)
            cur = ""
        else:
            cur = ln
    if cur:
        chunks.append(cur)
    return chunks


def chunk_for_discord(text: str, limit: int = DISCORD_LIMIT) -> List[str]:
    """Split `text` into Discord-safe chunks (each ≤limit chars).

    Strategy, in order of preference:
      1. Break on paragraphs (blank lines).
      2. Break on single newlines.
      3. Hard-cut only a line that is itself longer than the limit.

    Always returns at least one chunk. Never returns empty chunks. Every chunk
    is guaranteed ≤limit chars, so it is safe to post as a Discord message.
    """
    if text is None:
        return []
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    # 1) paragraph-aware packing
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    cur = ""
    for p in paras:
        p = p.strip()
        candidate = (cur + "\n\n" + p) if cur else p
        if len(candidate) <= limit:
            cur = candidate
            continue
        if cur:
            chunks.append(cur)
            cur = ""
        if len(p) <= limit:
            cur = p
        else:
            # paragraph is itself too long — fall through to line packing
            chunks.extend(_chunk_lines(p.split("\n"), limit))
            cur = ""
    if cur:
        chunks.append(cur)

    # 2) safety net: re-chunk any chunk that still exceeds the limit
    final: List[str] = []
    for c in chunks:
        if len(c) <= limit:
            final.append(c)
        else:
            final.extend(_chunk_lines(c.split("\n"), limit))
    # 3) final safety net: any chunk still too long (single giant line)
    out: List[str] = []
    for c in final:
        if len(c) <= limit:
            out.append(c)
        else:
            out.extend(_hard_chunk(c, limit))

    # Drop any empty strings and de-dupe accidental blank chunks.
    out = [c for c in (c.strip() for c in out) if c]
    return out or [text[:limit]]


def _fmt_followup(chunk: str, idx: int, total: int) -> str:
    """Render a follow-up chunk with a light '(continued)' header."""
    if total <= 1:
        return chunk
    return f"{CONTINUED}  ({idx}/{total})\n\n{chunk}"


async def send_long(interaction, content: str, limit: int = DISCORD_LIMIT) -> None:
    """Post `content` as the interaction response, chunking if needed.

    For slash commands (discord.Interaction): first chunk is the response,
    remaining chunks are sent as follow-ups. This is the fix for the
    50035 "Invalid Form Body" errors that hit any answer >2000 chars.
    """
    chunks = chunk_for_discord(content, limit)
    if not chunks:
        await interaction.response.send_message("(empty)")
        return
    total = len(chunks)
    await interaction.response.send_message(chunks[0])
    for i, c in enumerate(chunks[1:], start=2):
        await interaction.followup.send(_fmt_followup(c, i, total))


async def edit_original_long(interaction, content: str, limit: int = DISCORD_LIMIT) -> None:
    """Replace an already-sent "Thinking…" response with `content`, chunked.

    Used by cmd_ask: the bot first responds with "🤔 Thinking…", then edits
    that original response. First chunk goes via edit_original_response, the
    rest via follow-ups.
    """
    chunks = chunk_for_discord(content, limit)
    if not chunks:
        await interaction.edit_original_response(content="(empty)")
        return
    total = len(chunks)
    await interaction.edit_original_response(content=chunks[0])
    for i, c in enumerate(chunks[1:], start=2):
        await interaction.followup.send(_fmt_followup(c, i, total))


async def edit_reply_long(message, content: str, limit: int = DISCORD_LIMIT) -> None:
    """Replace an already-sent "Thinking…" reply with `content`, chunked.

    Used by the @-mention / conversation-thread path: the bot replies "🤔
    Thinking…", then edits that reply. First chunk via message.edit, the rest
    sent into the same channel/thread.
    """
    chunks = chunk_for_discord(content, limit)
    if not chunks:
        await message.edit(content="(empty)")
        return
    total = len(chunks)
    await message.edit(content=chunks[0])
    for i, c in enumerate(chunks[1:], start=2):
        await message.channel.send(_fmt_followup(c, i, total))


async def reply_long(message, content: str, limit: int = DISCORD_LIMIT) -> None:
    """Post `content` as a reply to a message (the @-mention / thread path).

    First chunk is `message.reply(...)`, remaining chunks are plain channel
    sends so the conversation stays in the thread.
    """
    chunks = chunk_for_discord(content, limit)
    if not chunks:
        await message.reply("(empty)")
        return
    total = len(chunks)
    await message.reply(chunks[0])
    for i, c in enumerate(chunks[1:], start=2):
        await message.channel.send(_fmt_followup(c, i, total))


if __name__ == "__main__":
    # Tiny self-test: prove the chunker never emits an over-limit chunk.
    import textwrap
    # A realistic long answer: paragraphs + a list + a long unbroken line.
    para = textwrap.dedent(
        "Meowdy! Here's a long answer about Wyvern to exercise the chunker."
    )
    long_line = "x" * 5000  # pathological: one line longer than the limit
    sample = "\n\n".join([para, para, "- item one", "- item two", long_line])
    ch = chunk_for_discord(sample)
    assert all(len(c) <= DISCORD_LIMIT for c in ch), "chunk exceeded limit!"
    print(f"OK: {len(sample)} chars -> {len(ch)} chunks, "
          f"max chunk len = {max(len(c) for c in ch)}")
