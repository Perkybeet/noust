# The built-in assistant

Noust can have an **assistant**: an AI that, when a person asks, looks at what is wrong with an
application and says what it found and what it would do. It is an AI and it says so; it is **optional,
off until an administrator turns it on**, and it uses **the model you choose** (a service that speaks the
OpenAI-compatible protocol, or a model that runs on your own machine or network). Noust sells no model and
runs no service for this: nothing leaves your server until you set it up, and what leaves is only what is
written under [What leaves your server](#what-leaves-your-server).

New in 4.3. The mechanics (the limits, the masking, the token it acts with, the audit trail) are tested
against a scripted model that answers what the test dictates. **How well a given model investigates is for
you to see**: the probe tells you whether a model makes a well formed call to a tool, not whether its advice
is good, and nothing here says an assistant cannot be misled by what it reads (see
[What it does not promise](#what-it-does-not-promise)).

```
printf '%s\n' "$KEY" | noust assistant enable --preset mistral --model MODEL --key-stdin
noust assistant status
noust assistant disable
```

## Choosing a model

The assistant speaks one protocol: **Chat Completions** as OpenAI-compatible servers speak it
(`POST <address>/chat/completions`, answered as a stream, with tools in `tool_calls`, and
`GET <address>/models`). Give it an address up to its version (`https://api.example.org/v1`), a model's
name and, when the service asks for one, a key. It depends on nothing a server may not honour
(`tool_choice`, `strict`, parallel calls); a server that does not stream, or refuses to stream with
tools, is asked again without streaming, and a provider that sends no usage is estimated and said to be.

A few services are on offer by name. Each address was read on the page of the service's own documentation,
and the place the data ends up is **only what that documentation says**:

| Name | Address | Key | Where the data ends up |
|---|---|---|---|
| `ollama` | `http://localhost:11434/v1` | no | This machine |
| `lm_studio` | `http://localhost:1234/v1` | no | This machine |
| `vllm` | `http://localhost:8000/v1` | if you started it with one | This machine |
| `mistral` | `https://api.mistral.ai/v1` | yes | The European Union, by default (Mistral's help center) |
| `ionos` | `https://openai.inference.de-txl.ionos.com/v1` | yes | Not stated by its documentation |
| `openrouter` | `https://openrouter.ai/api/v1` | yes | Not stated by its documentation: it routes to the provider of each model |

A preset promises nothing else: what tells the truth is the **probe**. Any other address works too
(`--base-url`). A service whose documentation says where it keeps data and that you have a contract with can
be declared `--location eu`; a public address can never be declared to be this machine or your network.

### Your own machine or network

A server on this machine or on your network is a place a request from a server running as root must not
go by default (it is how a request forgery reaches what is behind a firewall). Naming it is a decision you
take once: `--allow-private-host` lists the host in `security.allow_private_hosts`, which is the list that
already says so for every other feature. Plain `http://` is only for a host you listed; anything else
needs `https://`. The address is judged when you choose it **and on every request**, and the connection is
made to the address that was judged, so a name that starts to resolve somewhere else is refused.

### What a model of your own asks of the machine

No benchmark here was run by us; these are orders of magnitude from the guides and the benchmarks of
others, and the probe says what is true of your model:

- **4 GB, no GPU** (a small server): nothing useful. The model alone takes the memory, and a long log would
  take minutes to read. Use a provider's API, or a machine on your network.
- **16 GB, no GPU**: a model of 8 to 9 billion parameters can explain an error you show it, slowly, and
  tool calling is unreliable below that size. It is not a machine to investigate with.
- **A GPU or unified memory of 16 to 24 GB**: models around 20 billion parameters, or a 14 to 27 billion one
  in a quantised form, run well enough to call tools.
- **More than 100 billion parameters**: several GPUs or a server of their own; most people use an API.

When the model runs on this very machine and it has less than 16 GB and no GPU, the probe says so, with
this machine's figure and without promising how fast anything would be.

### The probe

On saving a provider, and when you ask (`noust assistant probe`, `POST /api/assistant/probe`), Noust asks
the provider what models it has and for **one call to a toy tool**, with a fixed sentence and no data of
yours. It says, as sentences with a code the console translates:

- whether the provider answers, and why not when it does not (the address is refused, the key is refused,
  nobody is listening);
- whether it has the model you chose;
- whether the model made the toy call well formed. **If it did not, the assistant only explains what it is
  shown: it neither investigates nor acts**, because what cannot be done safely is not offered;
- for a model on this machine, an honest word about memory.

The assistant is turned on only after a probe found the provider answering.

## What leaves your server

Only what a person asked, and what the assistant read to answer, over the connection above. The assistant
reads through the same tools as [the MCP endpoint](mcp.md) (list applications, read a log, diagnose, list
deployments, backups and certificates, and, when you allow it, restart or deploy), as the person's own
account, limited to the person's teams. The browser never talks to the provider: your server does.

- **No secret goes.** The values of every application's variables, Noust's own credentials, the provider's
  key and anything that looks like a credential (a token, a bearer header, a private key, a password in a
  URL) are removed from every message and every tool answer before anything is sent, by the code that
  removes them for the MCP endpoint. A value nobody told Noust is secret and that looks like nothing
  cannot be recognised: that is why the tools offer no way to reveal a secret in the first place.
- **For a provider that is not on this machine or your network**, emails and IP addresses (loopback and
  "any" aside) are also replaced by markers (`<email-1>`, `<ip-2>`) that are the same all through a
  conversation and that only your server can put back, in what the person reads and in the arguments of the
  calls the model makes. You can turn it off for a model of your own and for no other.
- **Text somebody else wrote** (logs, a README, a commit message) is sent marked as data, which helps the
  model and defends nothing by itself: the controls that matter are the server's.
- **The key** is sealed under the key file beside the configuration, never returned by the API (only that
  there is one and since when), and never in an argument, a job, an audit event or an approval.

[PRIVACY.md](PRIVACY.md) lists this request among the ones you ask for.

## With a fleet

The assistant belongs to the console a person works in. With a central it is turned on once, on the central, with
the central's model and limits, and nothing is installed or turned on in the servers the central manages. What it
looks at is the server chosen in the console, reached through the central like everything else the console shows
of that server, so each server still decides what its central may do on it (`noust fleet authorize --access`).

## Who it acts as

Not as the person's session. Each conversation acts with **a token of its own**:

- its owner is the person who asks, and it holds the **intersection** of what they hold and what one preset
  of the MCP tools needs (the one definition of what a client that controls may do);
- it never holds a permission of **its floor**, a closed list of its own, whatever a preset or a person holds:
  reading secrets, acting as root or on the host, a terminal, accounts, the audit trail, the security settings
  and the configuration (which is where its own settings, the policy of approval and the channels of
  notification live, and a channel is a way out for data), the fleet and the teams. It is not the list of what
  a client of the MCP endpoint outside is kept from: the assistant is asked to do what Noust knows how to do,
  so creating an application or restoring a copy is something it **proposes**, and the person carries it out in
  the console with their own session, where sudo mode and a second approver are asked;
- it sees the person's teams and nothing else, lasts a few hours and is **never asked for sudo mode**
  (`allow_elevated` is refused for it);
- what needs a person's confirmation (sudo mode, a second approver) is done by the person, in the console,
  with their own session: the assistant proposes, the server decides.

The audit trail names it as an actor of its own (`via: assistant`, with the person's name in the token's), and
records the tool it called, whether it worked and how large the answer was, a summary of each turn, and when a
limit stopped it. **Never the conversation.**

Using it is the permission **`assistant.use`**, which the administrator and operator roles hold and **no team
role** does in this version: the spending and the flow of data are the server's. On a server upgraded from 4.2
a role nobody edited has it by itself, and one you edited is offered it, which you take or leave.

## Limits

Always there, with a number: none can be removed, only changed (`settings.manage`, in sudo mode).

| Limit | Default |
|---|---|
| Steps in one answer | 20 |
| Input tokens in one conversation, added over every step | 400,000 |
| Spending in one conversation | 2 |
| Spending in a calendar month (UTC), everyone's | 20 |
| Changes the assistant may make for one person in an hour | 10 |

A warning comes at 80 % and the stop at all of it, in a sentence that says which limit it was and who changes it.
A limit cuts **between two steps and never in the middle of an action**: a step that crosses it is paid for and
written down, and the next one is refused. Spending is what the provider reports it used (`usage`) times a price
table **you** write per model, per million tokens, in the currency you say (`noust assistant configure --price
MODEL=IN,OUT --currency EUR`): prices change and no provider's list is Noust's to keep. A model with no price has
no spending limit, and the assistant says so; the limits of tokens apply. Without `usage` the tokens are
estimated from the characters, and it says so too. `noust assistant usage` and `GET /api/assistant/usage` show
what was spent.

## What stays

What a person said to the assistant stays in your server's store, for the person alone: nobody else reads,
extends or deletes it, and one who guesses its identifier gets the answer a conversation that does not exist
gets. What is written is already masked and bounded. It is kept `retention.assistant_days` (30 by default) and
its author can delete it any time (`DELETE /api/assistant/conversations/{id}`), which takes back the token it
acted with. Deleting it does not give the month's spending back.

## Turning it off

`noust assistant disable`, or `POST /api/assistant/off` in the console's sudo mode, turns it off and **revokes
every token it holds at once**. What a conversation in progress tries next fails with a sentence that says the
assistant was turned off.

## Under a security profile

With the `assistant_flows` control on (the ENS profile turns it on; see [COMPLIANCE.md](COMPLIANCE.md)) the
assistant is turned on only with a model on this machine or your network, or with a provider **declared an
authorized flow with its reason recorded** (`--flow-reason`). It is off by default whatever the profile says.

## What it does not promise

- That it is safe against prompt injection. A log or a README can say anything, and nobody has a way to stop
  a model from being misled by what it reads. What bounds the damage is not the model: the token holds at most
  what the person holds and never what is above, what changes something is limited per hour, and what needs
  sudo mode or a second person is done by a person.
- That its diagnosis is right. It shows what it read and what it concluded; the error is always shown as the
  system said it.
- That a model of your own is fast, or good, on the machine you have.
- A place for data a provider's documentation does not state.

## Reference

| | |
|---|---|
| `noust assistant status\|configure\|enable\|disable\|probe\|usage` | The command line. The key by standard input (`--key-stdin`) or `NOUST_ASSISTANT_API_KEY`, never as an argument. |
| `GET /api/assistant/settings`, `PUT /api/assistant/settings` | What is chosen, without the key; changing it needs `settings.manage` and sudo mode. |
| `POST /api/assistant/probe`, `POST /api/assistant/off` | The probe; turning it off. |
| `GET /api/assistant/usage`, `GET /api/assistant/presets` | What it cost; the providers by name. |
| `GET /api/assistant/conversations`, `GET`/`DELETE` `.../{id}` | A person's own conversations. |
| `assistant` in `GET /api/auth/session` | Whether it is on, whether this person may use it, whether its model calls tools, which model it is and where the data goes, or why it is not offered. |
| `retention.assistant_days` | Days its conversations are kept (30). |
| `security.assistant_flows` | The compliance control above. |
