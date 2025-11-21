# Botty

A [maubot](https://mau.bot) plugin, specifically designed for the [continuwuity](https://continuwuity.org) community
rooms. The user `@bot:continuwuity.org` runs this plugin.

## Commands

- `!resolve <server_name>` - Performs client-to-server & server-to-server resolution on a given server name, displaying
  some information about the server if successful, or presenting some error information if not.

### Auto-responses

- `MSCXXXX` - When `MSCXXXX` is found in a message, such as `MSC1234`, the bot will look up the corresponding Matrix
  Spec Change (MSC) and respond with a link, title, and list of tags associated with the MSC. Multiple MSCs in a single
  message will respond in the same list.
- `!XXX` or `#XXX` - When an issue or pull request is referenced in a message, such as `!1234` or `#5678`, the bot will 
  look up the corresponding issue or pull request on the default repository (configurable) and respond with a link,
  title, and tags associated with the issue or pull request. Multiple references in a single message will respond in the
  same list.
  The destination repo can be specified by prefixing the delimiter with the repo name, such as `continuwuity!1234` or
  `continuwuation/ruwuma!5678`. Cross-forge references are not supported yet.

## Questions etc

Ask in [`#offtopic:continuwuity.org`](https://matrix.to/#/#offtopic:continuwuity.org).
