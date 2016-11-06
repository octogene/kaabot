#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import sleekxmpp
import datetime
import locale
import dataset
import argparse
import getpass
import random
import sqlalchemy
from sleekxmpp.util.misc_ops import setdefaultencoding

setdefaultencoding('utf8')
locale.setlocale(locale.LC_ALL, 'fr_FR.UTF-8')


class KaaBot(sleekxmpp.ClientXMPP):
    def __init__(self, jid, password, database, room, nick):
        sleekxmpp.ClientXMPP.__init__(self, jid, password)

        self.room = room
        self.nick = nick
        self.online_timestamp = None
        self.db = dataset.connect('sqlite:///{db}'.format(db=database),
                                  engine_kwargs={'connect_args': {
                                      'check_same_thread': False}})

        self.users = self.db['user']
        # Initialize table with correct type.
        self.users.create_column('nick', sqlalchemy.String)
        self.users.create_column('offline_timestamp', sqlalchemy.DateTime)
        self.users.create_column('online_timestamp', sqlalchemy.DateTime)

        self.muc_log = self.db['muc_log']

        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)
        self.add_event_handler("muc::%s::got_online" % self.room,
                               self.muc_online)
        self.add_event_handler("muc::%s::got_offline" % self.room,
                               self.muc_offline)

    def session_start(self, event):
        self.send_presence()
        self.get_roster()
        self.plugin['xep_0045'].joinMUC(self.room, self.nick, wait=True)
        self.plugin['xep_0172'].publish_nick(self.nick)

    def message(self, msg):
        """Handles incoming messages.
        """
        # Private message
        if msg['type'] in ('chat', 'normal'):

            msg.reply("Thanks for sending\n%(body)s" % msg).send()

        # Public (MUC) message
        elif msg['type'] in ('groupchat'):
            if msg['mucnick'] != self.nick and msg['body'] == self.nick:
                self.send_help(msg['from'])
            elif msg['mucnick'] != self.nick and self.nick in msg['body']:
                if msg['body'].split()[1] in ['log', 'histo']:
                    self.send_log(msg['mucnick'], msg['from'])
                elif msg['body'].split()[1] in ['help', 'aide']:
                    self.send_help(msg['from'])
                elif msg['body'].split()[1] in ['uptime']:
                    self.send_uptime(msg['from'])
                else:
                    self.send_insults(msg['from'].bare)

            # Insert message in database with timestamp except for
            # bot messages or commands.
            elif msg['mucnick'] != self.nick:
                self.muc_log.insert(dict(datetime=datetime.datetime.now(),
                                         msg=msg['body'], user=msg['mucnick']))

    def send_help(self, dest):
        """Sends help messages to 'dest'
        """
        intro = ["Il a besoin d'aide le boulet ?"]
        cmd = ['([back]log|histo[rique]) : Historique des messages postés durant ton absence.',
            '(uptime) : Depuis combien de temps je suis debout ? ']
        mbody = '\n  '.join(intro + cmd)
        self.send_message(mto=dest,
                          mbody=mbody,
                          mtype='chat')

    def send_log(self, nick, dest):
        """Look up backlog for 'nick' and send it to 'dest'.
        """
        # Get offline timestamp from database and check if it exists.
        offline_timestamp = self.users.find_one(nick=nick)['offline_timestamp']
        if not offline_timestamp:
            logging.debug('KaaBot : No offline timestamp for {nick}.'.format(nick=nick))
            self.send_empty_log(dest)
            return
        else:
            logging.debug(
                'KaaBot : {nick} last seen on {date}'.format(nick=nick,
                                                             date=offline_timestamp))

        # Get online timestamp from database.
        online_timestamp = self.users.find_one(nick=nick)['online_timestamp']
        logging.debug(
            'KaaBot : {nick} last connection on {date}'.format(nick=nick,
                                                         date=online_timestamp))

        # Since filtered log is a generator we can't know in advance if
        # it will be empty. Creating filtered_log_empty allows us to act on
        # this event later.
        filtered_log_empty = True
        filtered_log = (log for log in self.muc_log if
                        offline_timestamp < log['datetime'] < online_timestamp)

        for log in filtered_log:
            filtered_log_empty = False
            body = log['msg']
            user = log['user']
            self.send_message(mto=dest,
                              mbody=': '.join((user, body)),
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

    def send_insults(self, dest):
        insults = ["Hé, tu peux apprendre à écrire ?",
                   "J'y comprends rien à ton charabia",
                   "T'as perdu l'usage de tes mains ?",
                   "/me sombre dans une crise existentielle."]

        mbody = insults[random.randint(0, len(insults) - 1)]
        self.send_message(mto=dest,
                          mbody=mbody,
                          mtype='groupchat')

    def muc_online(self, presence):
        """Handles MUC online presence.
           On bot connection gets called for each
           user in the MUC (bot included).
        """
        if presence['muc']['nick'] != self.nick:
            # Check if nick in database.
            if self.users.find_one(nick=presence['muc']['nick']):

                # Update nick online timestamp.
                self.users.update(dict(nick=presence['muc']['nick'],
                                       online_timestamp=datetime.datetime.now()),
                                  ['nick'])

                # Check if bot is connecting for the first time.
                if self.online_timestamp:
                    try:
                        offline_timestamp = self.users.find_one(nick=presence['muc']['nick'])['offline_timestamp']
                        msg = "La dernière fois que j'ai vu ta pomme c'était le {date}"
                        msg_formatted = msg.format(
                            date=datetime.datetime.strftime(offline_timestamp,
                                                            format="%c"))
                        self.send_message(mto=presence['from'].bare,
                                          mbody=msg_formatted,
                                          mtype='groupchat')
                    except TypeError:
                        msg = 'KaaBot : No offline timestamp yet for {nick}'
                        logging.debug(msg.format(nick=presence['muc']['nick']))
            else:
                self.users.insert(dict(nick=presence['muc']['nick'],
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
        if presence['muc']['nick'] != self.nick:
            self.users.update(dict(nick=presence['muc']['nick'],
                                   offline_timestamp=datetime.datetime.now()),
                              ['nick'])

if __name__ == '__main__':

    argp = argparse.ArgumentParser(
        description="Super Simple Silly Bot for Jabber")
    argp.add_argument('-d', '--debug', help='set logging to DEBUG',
                      action='store_const',
                      dest='loglevel', const=logging.DEBUG,
                      default=logging.INFO)
    argp.add_argument("-j", "--jid", dest="jid", help="JID to use")
    argp.add_argument("-p", "--password", dest="password",
                      help="password to use")
    argp.add_argument("-m", "--muc", dest="muc",
                      help="Multi User Chatroom to join")
    argp.add_argument("-n", "--nick", dest="nick", default='KaaBot',
                      help="Nickname to use in the chatroom (default: KaaBot)")
    argp.add_argument("-db", "--database", dest="database",
                      help="database to use", default="muc_log.db")

    args = argp.parse_args()

    if args.jid is None:
        args.jid = raw_input("Username: ")
    if args.password is None:
        args.password = getpass.getpass("Password: ")
    if args.muc is None:
        args.muc = raw_input("MUC: ")

    logging.basicConfig(level=args.loglevel,
                        format='%(levelname)-8s %(message)s')

    bot = KaaBot(args.jid, args.password, args.database, args.muc, args.nick)
    bot.register_plugin('xep_0045')
    bot.register_plugin('xep_0071')
    bot.register_plugin('xep_0172')
    bot.connect()
    bot.process(block=True)
