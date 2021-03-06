KaaBot
======

Super Simple Silly Bot for Jabber.

Requirements
------------

* Python 3.1+
* [SleekXMPP](http://sleekxmpp.com/) 1.3.1
* [dataset](https://dataset.readthedocs.io/) 0.7.0
* [PyXDG](https://freedesktop.org/wiki/Software/pyxdg/) 0.25

```
    pip install -r requirements
```

Installing
----------

Copy the main script in a directory that's in your `$PATH` (e.g.
`$HOME/.local/bin` or `/usr/local/bin`), copy the configuration files in a
`kaabot` subdirectory of a XDG config path (e.g.  `$HOME/.config/kaabot` or
`/etc/xdg/kaabot`), and you'll be ready to make your MUC a happier place!

```
cp kaabot.py $HOME/.local/bin/kaabot
mkdir $HOME/.config/kaabot
cp vocabulary.json $HOME/.config/kaabot
```

If no configuration file exists, one will be created automatically (typically
in `$HOME/.config/kaabot/config`) when KaaBot is launched for the first time.

Usage
-----

```
usage: kaabot [-h] [-d] [-b DATABASE] [-j JID] [-p PASSWORD] [-m MUC]
              [-n NICK] [-V VOCABULARY_FILE] [--welcome WELCOME]

optional arguments:
  -h, --help            show this help message and exit
  -d, --debug           set logging to DEBUG
  -b DATABASE, --database DATABASE
                        path to an alternative database; the '{muc}' string in
                        the name will be substituted with the MUC's name as
                        provided by the --muc option
  -j JID, --jid JID     JID to use
  -p PASSWORD, --password PASSWORD
                        password to use
  -m MUC, --muc MUC     Multi User Chatroom to join
  -n NICK, --nick NICK  nickname to use in the chatroom (default: KaaBot)
  -V VOCABULARY_FILE, --vocabulary_file VOCABULARY_FILE
                        path to an alternative vocabulary file
  --welcome WELCOME     welcome users joining the MUC (on/off, default: on)
```

License
-------

KaaBot is licensed under the terms of the Expat license (also known as the MIT
License). See the `COPYING` file.
