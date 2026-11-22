# Privacy

gmail-mcp is a program you run yourself, on your own computer or server. There is no service behind it: nobody but you operates it, and its author never receives any of your data.

**What it reads.** With your permission (the `gmail.readonly` scope), it reads your Gmail messages, threads, labels and attachments, only when an MCP client you connected asks for them. It can't send, change or delete anything.

**Where your data goes.** Straight from Google to the MCP client (the AI agent) you connected, and nowhere else. Whatever that client does with it is governed by that client's own privacy terms.

**What it stores.** Only your sign-in: the refresh token and your email address, kept in your OS keychain or in a file you choose. Mail is not stored, except attachments you explicitly ask it to save to a folder you configure.

**Removing access.** Run `gmail-mcp logout`, and remove the app at [myaccount.google.com/connections](https://myaccount.google.com/connections).

The use of information received from Google APIs follows the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including its Limited Use requirements.
