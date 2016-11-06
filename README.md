KaaBot
======

<img align="right" height="200" src="/octogene/kaabot/raw/master/logo.png"/>

Super Simple Silly Bot for Jabber.

Requirements
------------

* Python 3.1
* SleekXMPP & dataset modules:
```
    pip3 install sleekxmpp dataset
```

Usage
-----

```
usage: kaabot.py [-h] [-d] [-j JID] [-p PASSWORD] [-db DATABASE]


optional arguments:
  -h, --help            show this help message and exit
  -d, --debug           set logging to DEBUG
  -j JID, --jid JID     JID to use
  -p PASSWORD, --password PASSWORD
                        password to use
  -m MUC, --muc MUC     Multi User Chatroom to join
  -n NICK, --nick NICK  Nickname to use in the chatroom (default: KaaBot)
  -db DATABASE, --database DATABASE
                        database to use
```
