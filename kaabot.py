#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import sleekxmpp
import datetime
import locale
import dataset
import argparse
import getpass
from sleekxmpp.util.misc_ops import setdefaultencoding

setdefaultencoding('utf8')
locale.setlocale(locale.LC_ALL, 'fr_FR.UTF-8')


class KaaBot(sleekxmpp.ClientXMPP):
    def __init__(self, jid, password, database, room, nick):
        sleekxmpp.ClientXMPP.__init__(self, jid, password)

        self.room = room
        self.nick = nick

        self.db = dataset.connect('sqlite:///{db}'.format(db=database),
                                  engine_kwargs={'connect_args': {'check_same_thread': False}})
        self.users = self.db['user']
        self.muc_log = self.db['muc_log']

        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)
        # self.add_event_handler("groupchat_message", self.muc_message)
        self.add_event_handler("muc::%s::got_online" % self.room, self.muc_online)
        self.add_event_handler("muc::%s::got_offline" % self.room, self.muc_offline)

        for user in self.users.all():
            self.users.update(dict(nick=user['nick'], conn_time=None, last_seen=None), ['nick'])

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

            # TODO: Gestion fine des commandes pour le bot
            elif msg['mucnick'] != self.nick and self.nick in msg['body'] and msg['body'].endswith('/log'):
                self.send_log(msg['mucnick'], msg['from'])

            # Enregistre les messages dans la bdd exceptés ceux qui viennent du bot.
            elif msg['mucnick'] != self.nick:
                self.muc_log.insert(dict(datetime=datetime.datetime.now(), msg=msg['body'], user=msg['mucnick']))

    def send_help(self, dest):
        """Sends help messages to 'dest'
        """
        mbody = "Il a besoin d'aide le boulet ?\n- /log : voir les messages postés durant ton absence."
        self.send_message(mto=dest,
                          mbody=mbody,
                          mtype='chat')

    def send_log(self, nick, dest):
        """Look up backlog for 'nick' and send it to 'dest'.
        """
        last_seen = self.users.find_one(nick=nick)['last_seen']
        conn_time = self.users.find_one(nick=nick)['conn_time']

        filtered_log_empty = True
        filtered_log = (log for log in self.muc_log if log['datetime'] > last_seen and log['datetime'] < conn_time)
        try:
            for log in filtered_log:
                filtered_log_empty = False
                body = log['msg']
                user = log['user']
                self.send_message(mto=msg['from'],
                                  mbody=': '.join((user, body)),
                                  mtype='chat')
        except TypeError:
            logging.debug('Generator empty')

        if filtered_log_empty:
            self.send_message(mto=dest,
                              mbody='Aucun message depuis ta dernière venue. T\'es content ?',
                              mtype='chat')

    def muc_online(self, presence):
        if presence['muc']['nick'] != self.nick:
            print(presence)
            if self.users.find_one(nick=presence['muc']['nick']):
                self.users.update(dict(nick=presence['muc']['nick'], conn_time=datetime.datetime.now()), ['nick'])
                last_seen = self.users.find_one(nick=presence['muc']['nick'])['last_seen']
                msg = 'La dernière fois que j\'ai vu ta pomme c\'était le {date}'
                msg_formatted = msg.format(date=datetime.datetime.strftime(last_seen, format="%c"))
                self.send_message(mto=presence['from'].bare, mbody=msg_formatted, mtype='groupchat')
            else:
                self.users.insert(dict(nick=presence['muc']['nick'], conn_time=datetime.datetime.now()))

    def muc_offline(self, presence):
        if presence['muc']['nick'] != self.nick:
            if self.users.find_one(nick=presence['muc']['nick']):
                self.users.update(dict(nick=presence['muc']['nick'], last_seen=datetime.datetime.now()), ['nick'])
            else:
                self.users.insert(dict(nick=presence['muc']['nick'], last_seen=datetime.datetime.now()))


if __name__ == '__main__':

    argp = argparse.ArgumentParser(description="Super Simple Silly Bot for Jabber")
    argp.add_argument('-d', '--debug', help='set logging to DEBUG', action='store_const',
                      dest='loglevel', const=logging.DEBUG, default=logging.INFO)
    argp.add_argument("-j", "--jid", dest="jid", help="JID to use")
    argp.add_argument("-p", "--password", dest="password", help="password to use")
    argp.add_argument("-m", "--muc", dest="muc", help="Multi User Chatroom to join")
    argp.add_argument("-n", "--nick", dest="nick", default='KaaBot',
                      help="Nickname to use in the chatroom (default: KaaBot)")
    argp.add_argument("-db", "--database", dest="database", help="database to use", default='muc_log.db')

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
