from direct.directnotify import DirectNotifyGlobal
from toontown.uberdog.ClientServicesManagerUD import executeHttpRequest
import datetime
from direct.fsm.FSM import FSM
from direct.distributed.PyDatagram import PyDatagram
from direct.distributed.MsgTypes import *
from otp.ai.MagicWordGlobal import *
from direct.showbase.DirectObject import DirectObject

accountDBType = simbase.config.GetString('accountdb-type', 'developer')
if accountDBType == 'mysqldb':
    from passlib.hash import bcrypt
    import mysql.connector

class BanFSM(FSM):

    def __init__(self, air, avId, comment, duration):
        FSM.__init__(self, 'banFSM-%s' % avId)
        self.air = air
        self.avId = avId

        # Needed variables for the actual banning.
        self.comment = comment
        self.duration = duration
        self.DISLid = None
        self.accountId = None
        self.avName = None
        accountDBType = simbase.config.GetString('accountdb-type', 'developer')
        if accountDBType == 'mysqldb':
            mysql_username = simbase.config.GetString('mysql-username', 'toontown')
            mysql_password = simbase.config.GetString('mysql-password', 'password')
            mysql_db = simbase.config.GetString('mysql-db', 'toontown')
            mysql_host = simbase.config.GetString('mysql-host', '127.0.0.1')
            mysql_port = simbase.config.GetInt('mysql-port', 3306)
            mysql_ssl = simbase.config.GetBool('mysql-ssl', False)
            mysql_ssl_ca = simbase.config.GetString('mysql-ssl-ca', '')
            mysql_ssl_cert = simbase.config.GetString('mysql-ssl-cert', '')
            mysql_ssl_key = simbase.config.GetString('mysql-ssl-key', '')
            mysql_ssl_verify_cert = simbase.config.GetBool('mysql-ssl-verify-cert', False)
    
            # Lets try connection to the db
            if mysql_ssl:
                mysql_config = {
                  'user': mysql_username,
                  'password': mysql_password,
                  'db': mysql_db,
                  'host': mysql_host,
                  'port': mysql_port,
                  'client_flags': [ClientFlag.SSL],
                  'ssl_ca': mysql_ssl_ca,
                  'ssl_cert': mysql_ssl_cert,
                  'ssl_key': mysql_ssl_key,
                  'ssl_verify_cert': mysql_ssl_verify_cert
                }
            else:
                mysql_config = {
                  'user': mysql_username,
                  'password': mysql_password,
                  'db': mysql_db,
                  'host': mysql_host,
                  'port': mysql_port,
                }
    
            self.cnx = mysql.connector.connect(**self.config)
            self.cur = self.cnx.cursor(buffered=True)
            self.cnx.database = mysql_db
            self.update_ban = ("UPDATE Accounts SET canPlay = 0, banRelease = %s, banReason = %s where accountid = %s")

    def performBan(self, bannedUntil):
        accountDBType = simbase.config.GetString('accountdb-type', 'developer')
        if accountDBType == 'remote':
            executeHttpRequest('accounts/ban/', Id=self.accountId, Release=bannedUntil,
                           Reason=self.comment)
        if accountDBType == 'mysqldb':
            self.cur.execute(self.update_ban, (str(bannedUntil), self.comment), self.accountId)
            self.cnx.commit()

    def ejectPlayer(self):
        av = self.air.doId2do.get(self.avId)
        if not av:
            return

        # Send the client a 'CLIENTAGENT_EJECT' with the players name.
        datagram = PyDatagram()
        datagram.addServerHeader(
                av.GetPuppetConnectionChannel(self.avId),
                self.air.ourChannel, CLIENTAGENT_EJECT)
        datagram.addUint16(152)
        datagram.addString(self.avName)
        simbase.air.send(datagram)

    def dbCallback(self, dclass, fields):
        if dclass != self.air.dclassesByName['AccountAI']:
            return

        self.accountId = fields.get('ACCOUNT_ID')

        if not self.accountId:
            return

        date = datetime.date.today()
        if simbase.config.GetBool('want-bans', True):
            if self.duration == 0:
                bannedUntil = "0000-00-00" # Terminated.
            else:
                bannedUntil = date + datetime.timedelta(days=self.duration)

            self.duration = None
            self.performBan(bannedUntil)

    def getAvatarDetails(self):
        av = self.air.doId2do.get(self.avId)
        if not av:
            return

        self.DISLid = av.getDISLid()
        self.avName = av.getName()

    def log(self):
        simbase.air.writeServerEvent('ban', self.accountId, self.comment)

    def cleanup(self):
        self.air = None
        self.avId = None

        self.DISLid = None
        self.avName = None
        self.accountId = None
        self.comment = None
        self.duration = None
        self = None

    def enterStart(self):
        self.getAvatarDetails()
        self.air.dbInterface.queryObject(self.air.dbId, self.DISLid,
                                         self.dbCallback)
        self.ejectPlayer()

    def exitStart(self):
        self.log()
        self.cleanup()

    def enterOff(self):
        pass

    def exitOff(self):
        pass


class BanManagerAI(DirectObject):
    notify = DirectNotifyGlobal.directNotify.newCategory('BanManagerAI')

    def __init__(self, air):
        self.air = air
        self.banFSMs = {}

    def ban(self, avId, duration, comment):
        self.banFSMs[avId] = BanFSM(self.air, avId, comment, duration)
        self.banFSMs[avId].request('Start')

        self.acceptOnce(self.air.getAvatarExitEvent(avId), self.banDone, [avId])

    def banDone(self, avId):
        self.banFSMs[avId].request('Off')
        self.banFSMs[avId] = None


@magicWord(category=CATEGORY_MODERATOR, types=[str])
def kick(reason='No reason specified'):
    """
    Kick the target from the game server.
    """
    target = spellbook.getTarget()
    if target == spellbook.getInvoker():
        return "You can't kick yourself!"
    datagram = PyDatagram()
    datagram.addServerHeader(
        target.GetPuppetConnectionChannel(target.doId),
        simbase.air.ourChannel, CLIENTAGENT_EJECT)
    datagram.addUint16(155)
    datagram.addString('You were kicked by a moderator for the following reason: %s' % reason)
    simbase.air.send(datagram)
    return "Kicked %s from the game server!" % target.getName()


@magicWord(category=CATEGORY_MODERATOR, types=[str, int])
def ban(reason, duration):
    """
    Ban the target from the game server.
    """
    target = spellbook.getTarget()
    if target == spellbook.getInvoker():
        return "You can't ban yourself!"
    if reason not in ('hacking', 'language', 'other'):
        return "'%s' is not a valid reason." % reason
    simbase.air.banManager.ban(target.doId, duration, reason)
    return "Banned %s from the game server!" % target.getName()

