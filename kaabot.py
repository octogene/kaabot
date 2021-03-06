#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (c) 2016 Bogdan Cordier <ooctogene@gmail.com>
#                and Matteo Cypriani <mcy@lm7.fr>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import json
import logging
import sleekxmpp
import datetime
import locale
import dataset
import configargparse
import getpass
import os
import random
import sqlalchemy
import xdg.BaseDirectory
import pathlib

locale.setlocale(locale.LC_ALL, 'fr_FR.UTF-8')

default_vocabulary = {
    'help': ["My vocabulary empty, I can't help you."],
    'empty_log': ["No log for you."],
    'gossips': ["{nick} is reading the back log."],
    'greetings': ["/me is here!"],
    'insults': ['If I had vocabulary, I would insult {nick}.'],
    'uptime': ["I'm up for {uptime}."],
    'welcome': ["{nick}'s last connection: {date}."],
    # Responses to direct messages (not on a MUC):
    'refusals': ["I don't accept direct messages. Try on a MUC."],
}


class KaaBot(sleekxmpp.ClientXMPP):
    def __init__(self, jid, password, database, muc, nick, vocabulary_file,
                 welcome):
        sleekxmpp.ClientXMPP.__init__(self, jid, password)

        self.muc = muc
        self.nick = nick
        self.online_timestamp = None
        database_path = self.find_database(database, muc)
        self.db = dataset.connect('sqlite:///{db}'.format(db=database_path),
                                  engine_kwargs={'connect_args': {
                                      'check_same_thread': False}})

        self.vocabulary = self.init_vocabulary(vocabulary_file)

        self.welcome = welcome

        self.users = self.db['user']
        # Initialize table with correct type.
        self.users.create_column('nick', sqlalchemy.String)
        self.users.create_column('offline_timestamp', sqlalchemy.DateTime)
        self.users.create_column('online_timestamp', sqlalchemy.DateTime)

        self.muc_log = self.db['muc_log']

        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)
        self.add_event_handler("muc::%s::got_online" % self.muc,
                               self.muc_online)
        self.add_event_handler("muc::%s::got_offline" % self.muc,
                               self.muc_offline)

    @staticmethod
    def find_database(database, muc):
        """Returns the path to the database to use for the given MUC.

        If `database` is empty, the file name is generated from the MUC's name
        `muc` in the first "kaabot" XDG data dir (usually
        `$HOME/.local/share/kaabot/`). If the XDG data dir can't be located, the
        database is created/open in the work directory.

        If it contains a value, it is assumed to be the path to the database. If
        the name contains the string "{muc}", it will be substituted with the
        MUC's name.

        The returned path may or may not exist in the file system.
        """
        if database:
            return database.format(muc=muc)

        data_dir = xdg.BaseDirectory.save_data_path("kaabot")
        database = "{muc}.db".format(muc=muc)
        return os.path.join(data_dir, database)

    @staticmethod
    def init_vocabulary(vocabulary_file):
        """Reads the vocabulary from a JSON file.

        If vocabulary_file is empty (i.e. the user didn't use the --vocabulary
        option), a file named "vocabulary.json" is searched in the first
        existing XDG "kaabot" config path, in order of preference (usually
        $HOME/.config/kaabot/, then /etc/xdg/kaabot/).

        If vocabulary_file contains a value, it is considered to be the path to
        a valid vocabulary file.

        Error handling:
        - If the user-specified file can't be opened, the program will crash.
        - Ditto if the XDG-found file exists but can't be opened.
        - If the XDG directory cannot be detected, "vocabulary.json" is searched
          in the work directory.
        - In case of parsing error (the file exists but is invalid JSON),
          minimalistic vocabulary is set.
        - Ditto if the user didn't use --vocabulary and no vocabulary file is
          found in the XDG config path.
        """
        if not vocabulary_file:
            config_dir = xdg.BaseDirectory.load_first_config("kaabot")
            full_path = os.path.join(config_dir, "vocabulary.json")
            if os.path.exists(full_path):
                vocabulary_file = full_path

        if vocabulary_file:
            return KaaBot.read_vocabulary_file(vocabulary_file)
        else:
            return default_vocabulary

    @staticmethod
    def read_vocabulary_file(vocabulary_file):
        """Actually read and parse the vocabulary file.
        """
        try:
            fd = open(vocabulary_file, encoding='UTF-8')
        except IOError:
            logging.error("Can't open vocabulary file {filename}!"
                          .format(filename=vocabulary_file))
            raise

        try:
            vocabulary = json.load(fd)
            fd.close()
        except ValueError:  # json.JSONDecodeError in Python >= 3.5
            logging.warning(("Invalid JSON vocabulary file '{filename}'. "
                             "Minimal vocabulary will be set.")
                            .format(filename=vocabulary_file))
            vocabulary = default_vocabulary

        return vocabulary

    def session_start(self, event):
        self.send_presence()
        self.get_roster()
        self.plugin['xep_0045'].joinMUC(self.muc, self.nick, wait=True)
        self.plugin['xep_0172'].publish_nick(self.nick)

    def message(self, msg):
        """Handles incoming messages.
        """
        # Private message
        if msg['type'] in ('chat', 'normal'):
            # Don't accept private messages unless they are initiated from a MUC
            if msg['from'].bare != self.muc:
                msg.reply(self.pick_sentence('refusals')).send()
                return

            # Message's author info
            dest = msg['from']
            nick = msg['from'].resource

            command = msg['body'].strip()
            self.parse_command(command, nick, dest, priv=True)

        # Public (MUC) message
        elif msg['type'] in ('groupchat'):
            # Message's author info
            dest = msg['from']
            nick = msg['mucnick']

            # Insert message in database with timestamp
            self.muc_log.insert(dict(datetime=datetime.datetime.now(),
                                     msg=msg['body'], user=nick))

            # Stop dealing with this message if we sent it
            if msg['mucnick'] == self.nick:
                return

            splitbody = msg['body'].split(sep=self.nick, maxsplit=1)

            # The message starts or ends with the bot's nick
            if len(splitbody) == 2:
                if splitbody[1]:
                    # Bot's nick is at the beginning
                    command = splitbody[1]
                else:
                    # Bot's nick is at the end
                    command = splitbody[0]
                command = command.lstrip('\t :, ').rstrip()
                self.parse_command(command, nick, dest)

            # The bot's nick was used in the middle of a message
            elif self.nick in msg['body']:
                self.send_insult(nick, dest.bare)

    def parse_command(self, command, nick, dest, priv=False):
        """Parses a command sent by dest (nick).

        `priv` should be True if the bot was contacted through a private
        message, False if analysing a public message. In any case, the bot may
        report publicly information about the commands processed.
        """
        if not command:  # original message was just the bot's name
            self.send_help(dest)
        elif command in ['log', 'histo']:
            self.send_log(nick, dest, echo=priv)
        elif command in ['help', 'aide']:
            self.send_help(dest)
        elif command in ['uptime']:
            self.send_uptime(dest, priv)
        else:
            self.send_insult(nick, dest.bare)

    def send_help(self, dest):
        """Sends help messages to 'dest'.
        """
        mbody = '\n  '.join(self.vocabulary['help'])
        self.send_message(mto=dest,
                          mbody=mbody,
                          mtype='chat')

    def send_log(self, nick, dest, echo=False):
        """Look up backlog for 'nick' and send it to 'dest'.
        """
        if echo:
            gossip = self.pick_sentence('gossips').format(nick=nick)
            self.send_message(mto=dest.bare,
                              mbody=gossip,
                              mtype='groupchat')

        # Get offline timestamp from database and check if it exists.
        offline_timestamp = self.users.find_one(nick=nick)['offline_timestamp']
        if not offline_timestamp:
            logging.debug(('KaaBot : No offline'
                           ' timestamp for {nick}.').format(nick=nick))
            self.send_empty_log(dest)
            return
        else:
            logging.debug(
                ('KaaBot : {nick} '
                 'last seen on {date}').format(nick=nick,
                                               date=offline_timestamp))

        # Get online timestamp from database.
        online_timestamp = self.users.find_one(nick=nick)['online_timestamp']
        logging.debug(('KaaBot : {nick} last'
                       ' connection on {date}').format(nick=nick,
                                                       date=online_timestamp))

        # Since filtered log is a generator we can't know in advance if
        # it will be empty. Creating filtered_log_empty allows us to act on
        # this event later.
        filtered_log_empty = True
        filtered_log = (log for log in self.muc_log if
                        offline_timestamp < log['datetime'] < online_timestamp)

        for log in filtered_log:
            filtered_log_empty = False
            log_message = "[{:%H:%M}] {}: {}".format(log['datetime'],
                                                     log['user'],
                                                     log['msg'])
            self.send_message(mto=dest,
                              mbody=log_message,
                              mtype='chat')

        # Send message if filtered_log is still empty.
        if filtered_log_empty:
            logging.debug('KaaBot : Filtered backlog empty.')
            self.send_empty_log(dest)

    def send_empty_log(self, dest):
        """Send message if backlog empty.
        """
        mbody = self.pick_sentence('empty_log')
        self.send_message(mto=dest,
                          mbody=mbody,
                          mtype='chat')

    def send_uptime(self, dest, priv=False):
        """Sends the uptime to `dest`.

        If `priv` is true, the message is sent privately, otherwise it's send on
        the MUC.
        """
        uptime = str(datetime.datetime.now() - self.online_timestamp)
        mbody = self.pick_sentence('uptime').format(uptime=uptime)
        if priv:
            self.send_message(mto=dest,
                              mbody=mbody,
                              mtype='chat')
        else:
            self.send_message(mto=dest.bare,
                              mbody=mbody,
                              mtype='groupchat')

    def send_insult(self, nick, dest):
        """Sends an insult about `nick` to `dest`.
        """
        insult = self.pick_sentence('insults').format(nick=nick)
        self.send_message(mto=dest,
                          mbody=insult,
                          mtype='groupchat')

    def send_welcome(self, nick, dest, date):
        msg = self.pick_sentence('welcome').format(nick=nick, date=date)
        self.send_message(mto=dest,
                          mbody=msg,
                          mtype='groupchat')

    def pick_sentence(self, type):
        """Returns a random sentence picked in the loaded vocabulary.

        `type` can be any known category of the vocabulary file, e.g. 'insults'.
        No substitution is done to the returned string.
        """
        voc = self.vocabulary[type]
        i = random.randint(0, len(voc) - 1)
        return voc[i]

    def muc_online(self, presence):
        """Handles MUC online presence.
           On bot connection gets called for each
           user in the MUC (bot included).
        """
        nick = presence['muc']['nick']
        if nick != self.nick:
            # Check if nick in database.
            if self.users.find_one(nick=nick):

                # Update nick online timestamp.
                self.users.update(dict(nick=nick,
                                       online_timestamp=datetime.datetime.now()),
                                  ['nick'])

                # Check if bot is connecting for the first time.
                if self.online_timestamp:
                    try:
                        user = self.users.find_one(nick=nick)
                        offline_timestamp = user['offline_timestamp']
                        date = datetime.datetime.strftime(offline_timestamp,
                                                          format="%c")
                        logging.debug('KaaBot : user {} connected, last seen {}'
                                      .format(nick, date))
                        if self.welcome:
                            dest = presence['from'].bare
                            self.send_welcome(nick, dest, date)

                    except TypeError:
                        msg = 'KaaBot : No offline timestamp yet for {nick}'
                        logging.debug(msg.format(nick=nick))
            else:
                self.users.insert(dict(nick=nick,
                                       online_timestamp=datetime.datetime.now()))
        else:
            # Set bot online timestamp.
            self.online_timestamp = datetime.datetime.now()
            self.send_message(mto=presence['from'].bare,
                              mbody=self.pick_sentence('greetings'),
                              mtype='groupchat')

    def muc_offline(self, presence):
        """Handles MUC offline presence.
        """
        nick = presence['muc']['nick']
        if nick != self.nick:
            self.users.update(dict(nick=nick,
                                   offline_timestamp=datetime.datetime.now()),
                              ['nick'])


def str_to_bool(text):
    """Converts a string to a boolean.

    Raises an exception if the string does not describe a boolean value.
    """
    text = text.lower()
    if text in ["on", "true", "1"]:
        return True
    elif text in ["off", "false", "0"]:
        return False
    raise TypeError


if __name__ == '__main__':

    config_dir = xdg.BaseDirectory.save_config_path("kaabot")
    config_file = os.path.join(config_dir, 'config')

    argp = configargparse.ArgParser(default_config_files=[config_file],
        description="Super Simple Silly Bot for Jabber.")
    argp.add_argument('-d', '--debug', help="set logging to DEBUG",
                      action='store_const',
                      dest='debug', const=logging.DEBUG,
                      default=logging.INFO)
    argp.add_argument("-b", "--database", dest="database",
                      help="path to an alternative database; the '{muc}' string"
                           " in the name will be substituted with "
                           "the MUC's name as provided by the --muc option")
    argp.add_argument("-j", "--jid", dest="jid", help="JID to use")
    argp.add_argument("-p", "--password", dest="password",
                      help="password to use")
    argp.add_argument("-m", "--muc", dest="muc",
                      help="Multi User Chatroom to join")
    argp.add_argument("-n", "--nick", dest="nick", default='KaaBot',
                      help="nickname to use in the chatroom (default: KaaBot)")
    argp.add_argument("-V", "--vocabulary_file", dest="vocabulary_file",
                      help="path to an alternative vocabulary file")
    argp.add_argument("--welcome", dest="welcome", default="on",
                      type=str_to_bool,
                      help="welcome users joining the MUC (on/off, default: on)")

    args = argp.parse_args()

    if args.jid is None:
        args.jid = input("Username: ")
    if args.password is None:
        args.password = getpass.getpass("Password: ")
    if args.muc is None:
        args.muc = input("MUC: ")

    logging.basicConfig(level=args.debug,
                        format='%(levelname)-8s %(message)s')

    try:
        pathlib.Path(config_file).touch(mode=0o600, exist_ok=False)
        arguments = vars(args)
        arguments.pop("debug")
        with open(config_file, 'w') as f:
                f.writelines('{}= {}\n'.format(k, v) for k, v
                             in arguments.items() if v)
    except FileExistsError:
        logging.debug('Config file exists.')

    bot = KaaBot(args.jid, args.password, args.database,
                 args.muc, args.nick, args.vocabulary_file,
                 args.welcome)
    bot.register_plugin('xep_0045')
    bot.register_plugin('xep_0071')
    bot.register_plugin('xep_0172')
    bot.connect()
    bot.process(block=True)
