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
import argparse
import getpass
import os
import random
import sqlalchemy
import xdg.BaseDirectory

locale.setlocale(locale.LC_ALL, 'fr_FR.UTF-8')

default_vocabulary = {
    'insults': ['If I had vocabulary, I would insult {nick}.'],
    # Responses to direct messages (not on a MUC):
    'refusals': ["I don't accept direct messages. Try on a MUC."],
}


class KaaBot(sleekxmpp.ClientXMPP):
    def __init__(self, jid, password, database, muc, nick, vocabulary_file):
        sleekxmpp.ClientXMPP.__init__(self, jid, password)

        self.muc = muc
        self.nick = nick
        self.online_timestamp = None
        database_path = self.find_database(database, muc)
        self.db = dataset.connect('sqlite:///{db}'.format(db=database_path),
                                  engine_kwargs={'connect_args': {
                                      'check_same_thread': False}})

        self.vocabulary = self.init_vocabulary(vocabulary_file)

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
        `$HOME/.local/share/kaabot/`).

        If it contains a value, it is assumed to be the path to the database. If
        the name contains the string "{muc}", it will be substituted with the
        MUC's name.

        The returned path may or may not exist in the file system.
        """
        if database:
            return database.format(muc=muc)

        data_dir = xdg.BaseDirectory.save_data_path("kaabot")
        database = "{muc}.db".format(muc=muc)
        return "{}/{}".format(data_dir, database)

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
        - In case of parsing error (the file exists but is invalid JSON),
          minimalistic vocabulary is set.
        - Ditto if the user didn't use --vocabulary and no vocabulary file is
          found in the XDG config path.
        """
        if not vocabulary_file:
            config_dir = xdg.BaseDirectory.load_first_config("kaabot")
            full_path = config_dir + "/vocabulary.json"
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
        except OSError:
            logging.error("Can't open vocabulary file {filename}!"
                          .format(filename=vocabulary_file))
            raise

        try:
            vocabulary = json.load(fd)
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
            self.parse_command(command, nick, dest, echo=True)

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

    def parse_command(self, command, nick, dest, echo=False):
        """Parses a command sent by dest (nick).

        If echo is True, the bot may report publicly information about the
        commands processed.
        """
        if not command:  # original message was just the bot's name
            self.send_help(dest)
        elif command in ['log', 'histo']:
            self.send_log(nick, dest, echo)
        elif command in ['help', 'aide']:
            self.send_help(dest)
        elif command in ['uptime']:
            self.send_uptime(dest)
        else:
            self.send_insult(nick, dest.bare)

    def send_help(self, dest):
        """Sends help messages to 'dest'.
        """
        intro = ["Il a besoin d'aide le boulet ?"]
        cmd = [('(log|histo) : Historique'
                'des messages postés durant ton absence.'),
               '(uptime) : Depuis combien de temps je suis debout ?']
        mbody = '\n  '.join(intro + cmd)
        self.send_message(mto=dest,
                          mbody=mbody,
                          mtype='chat')

    def send_log(self, nick, dest, echo=False):
        """Look up backlog for 'nick' and send it to 'dest'.
        """
        if echo:
            gossip = nick + " consulte l'historique en loucedé !"
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
            log_message = ': '.join((log['user'], log['msg']))
            self.send_message(mto=dest,
                              mbody=log_message,
                              mtype='chat')

        #  Send message if filtered_log is still empty.
        if filtered_log_empty:
            logging.debug('KaaBot : Filtered backlog empty.')
            self.send_empty_log(dest)

    def send_empty_log(self, dest):
        """Send message if backlog empty.
        """
        mbody = "Aucun message depuis ta dernière venue. T'es content ?"
        self.send_message(mto=dest,
                          mbody=mbody,
                          mtype='chat')

    def send_uptime(self, dest):
        uptime = str(datetime.datetime.now() - self.online_timestamp)
        mbody = "Je suis debout depuis {uptime}".format(uptime=uptime)
        self.send_message(mto=dest,
                          mbody=mbody,
                          mtype='chat')

    def send_insult(self, nick, dest):
        insult = self.pick_sentence('insults').format(nick=nick)
        self.send_message(mto=dest,
                          mbody=insult,
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
                        msg = ("Salut {nick}, la dernière fois"
                               " que j'ai vu ta pomme c'était le {date}.")
                        msg_formatted = msg.format(nick=nick,
                                                   date=datetime.datetime.strftime(
                                                       offline_timestamp,
                                                       format="%c"))
                        self.send_message(mto=presence['from'].bare,
                                          mbody=msg_formatted,
                                          mtype='groupchat')
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
                              mbody='/me est dans la place !',
                              mtype='groupchat')

    def muc_offline(self, presence):
        """Handles MUC offline presence.
        """
        nick = presence['muc']['nick']
        if nick != self.nick:
            self.users.update(dict(nick=nick,
                                   offline_timestamp=datetime.datetime.now()),
                              ['nick'])


if __name__ == '__main__':

    argp = argparse.ArgumentParser(
        description="Super Simple Silly Bot for Jabber")
    argp.add_argument('-d', '--debug', help="set logging to DEBUG",
                      action='store_const',
                      dest='loglevel', const=logging.DEBUG,
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
    argp.add_argument("-V", "--vocabulary", dest="vocabulary_file",
                      help="path to an alternative vocabulary file")

    args = argp.parse_args()

    if args.jid is None:
        args.jid = input("Username: ")
    if args.password is None:
        args.password = getpass.getpass("Password: ")
    if args.muc is None:
        args.muc = input("MUC: ")

    logging.basicConfig(level=args.loglevel,
                        format='%(levelname)-8s %(message)s')

    bot = KaaBot(args.jid, args.password, args.database,
                 args.muc, args.nick, args.vocabulary_file)
    bot.register_plugin('xep_0045')
    bot.register_plugin('xep_0071')
    bot.register_plugin('xep_0172')
    bot.connect()
    bot.process(block=True)
