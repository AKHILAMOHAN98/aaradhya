'''
Created on Mar 22, 2010

@author: Nathan Roy (natroy@cisco.com)
@revision: Matt Herbert (matherbe@cisco.com) Nov 04, 2010
@revision: Matt Herbert (matherbe@cisco.com) July 27, 2011

@note: This file contains all of the classes required to do the logic around
handling commands from the automation server.  This module is meant to contain
ALL of the command classes so it can be imported into a manager class and
reflection can be used to work with them.

ADDING COMMANDS

If you need to add a command simply follow the format you see below. You should
inherit from BaseCommand, and name your command Class so it has the same name
(plus '_command') as the command string that will be passed on the socket.
So, if you need to add a new command called 'widget', you would create a class
like this:

    class widget_command(BaseCommand):
        """One line description of what widget command does
        <arg1> <arg2>
        Detailed, multi line description of what widget does, including
        details of what each argument is for

            arg1 - first argument detailed description

            arg2 - second argument detailed description
        """
        def __init__(this,user,args=None):
            this.user = user
            this.args = this._parse_args(args,['arg1','arg2','arg3'])

        def do_command(this,msg):
            print "widget is in play"

You MUST implement the do_command() method, or the base class will cause an
exception to be raised.

You may optionally implement a do_post_socket_send_actions() method, which will
be executed after the Service has responded to the client. However, be
forewarned, that if implemented this method *will* be executed no matter what!
so even if your do_command() raises an exception, the do_post_socket_send_action
will still execute! It's up to you (using state variables) to control whether
or not the code in your method should be executed.

The docstring should be in the same format as above. The first line should be
a brief 1 line synopsis of what the command does. The second line should be a
list of any arguments the command takes (leave it blank if the command does not
take any arguments). The remaining lines should be a detailed description of
the command and its arguments.

As for the argument handling, you should call into the _parse_args() method
(provided by BaseCommand), even if your command does not have/need any args.
The arguments to _parse_args() are 1) the args variable received in __init__,
and 2) a list (in order) of the expected arguments (empty list for no args).
Doing this will make the command backwards compatible with all the different
protocol versions the service supports .... and, in theory, forwards compatible
with future protocol versions.

'''
import base64
import time
import ftplib
import os
import re
import config
import NetDevice
import subprocess
import sys
import thread
import cPickle

import cfg
import Kickstart
import ServiceRunnerCore
import HostInfo

HOST_INFO = HostInfo.HostInfo()

if HOST_INFO.isMac():
   import NetConfig
   NET_CONFIG = NetConfig.NetConfig()

#http://pysvn.tigris.org/docs/pysvn_prog_ref.html#pysvn_client_callback_ssl_server_trust_prompt
#Return a tuple for trust certificate. If we get read permission without login, return value will be change to False, 1, True
def ssl_server_trust_prompt( trust_dict ):
    return True, 1, True
#Return a tuple for credential.
def svn_credentials (realm, username, may_save):
    return True, "<user name>", "<password>", False

class BaseCommand(object):
    """This class is the Base class which all command objects derive from.
    This will be used with reflection to allow us to respond to commands
    more easily."""

    # The runner (so we can get information from the environment)
    runner = None

    def do_command(this, msg, user, command):
        """This method is what is called to actually run the command."""
        raise NotImplementedError("The do_command method was called on"\
                                  " BaseCommand, meaning the command it was"\
                                  " called on did not implement that method")

    def do_post_socket_send_actions(this):
        """This method is to be called after the response is sent to the server.
        This can be used to exit after sending data to the server on a restart
        command and other purposes which require fine grained control as to
        when things should occur
        """
        pass

    def _parse_args(this,args,expected):
        """This method is responsible for parsing the arguments that are
        passed to individual commands. The problem is that protocol 00 and 01
        arguments were passed in as a string which had to be split up and
        processed by each command. In protcol 02 or newer, the arguments are
        constructed from a pickled dictionary.

        So, this method will look at the args variable, and if it is:

          - None create a dictionary with any keys from 'expected' populated
            with None values. If expected is None, then an empty dictionary
            will be returned. If we recieved None, in most cases it would
            mean the command did not require any aguments, but it also could
            mean the command had optional arguments which were not provided.
            In any case, the only time this should be None, is if the protocol
            used was v00 or v01.

          - If args is a string it will be parsed and a dictionary will be
            constructed using the positional items in the passed in expected
            array.

          - If args is a dictionary, this method will verify all key names from
            the expected array are present in the dictionary.
        """
        if args is None:
            # this command does not require any args, or has optional only args
            data = dict()
            for key in expected:
                data[key] = None
            return data

        elif type(args) is str:
            # this command is parsing a protocol version 00 arg list
            data   = dict()
            values = args.split()
            i=0
            for key in expected:
                # if we got an expected key, and we don't have enough values,
                # it's ok ... that is how version 00 protocol allowed for
                # "optional" arguments. Set it to None
                if i > len(values)-1:
                    data[key] = None
                else:
                    data[key] = values[i]
                i+=1

            # Check to make sure there were no left over values that didn't get
            # prcoessed
            if i < len(values) -1:
                raise RuntimeError('%s to many arguemnts: expected %d got %d' \
                        %(this.__class__.__name__, len(expected),len(values)))

            # normalize any "none" string values to python None type
            for key in data.keys():
                if type(data[key]) is str and data[key].lower() == 'none':
                    data[key] = None
            return data

        elif type(args) is dict:
            # this command is verifying a protocol version 02 or newer arg list
            for key in expected:
                if key not in args:
                    raise RuntimeError('%s missing argument %s' \
                            %(this.__class__.__name__, key))

            # normalize any "none" string values to python None type
            for key in args.keys():
                if type(args[key]) is str and args[key].lower() == 'none':
                    args[key] = None
            return args

        else:
            raise RuntimeError('args data type %s not supported' %type(args))

class history_command(BaseCommand):
    """Show a history of the last 20 commands recieved

    This command will show a history of the last 20 commands received by
    the ServiceRunner, excepting 'status', 'getcases', and 'hostinfo' commands,
    which are not included due to their frequency.
    """
    name = 'history'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg, history):
        xml = "<history>\n"
        for cmd in history:
            xml += "  <command>%s %s %s</command>\n" \
                    %(cmd.user, cmd.name, cmd.args)
        xml += "</history>\n"
        msg.code = 'ack'
        msg.data = xml

class quit_command(BaseCommand):
    """Tell the service to exit immediatley

    Calling this method will set the service ruuner main thread to quit,
    which should (in most cases) cause the ServiceRunner service to exit
    """
    name = 'quit'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        if not this.runner.check_acl(this.user,msg):
            return
        this.runner.QUIT = True
        msg.code = 'ack'

class svnupdate_command(BaseCommand):
    """SVN update the code, and restart the service
    <revision>
    This command will perform an SVN update on the local copy of the
    automation code. The code will be updated to the given revision if
    specified, otherwise it'll be updated to HEAD. The service runner
    will be automatically restarted after update.
        revision - (optional) specifies which revision to update to.
    """
    name = 'svnupdate'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])
        this.__restart_command = None

    def do_command(this, msg):
        expected_revision = this.args.get('revision')
        if HOST_INFO.isMac():
            try:
                if expected_revision is None:
                    cmd = 'svn update %s'% cfg.BASEDIR
                else:
                    cmd = 'svn update %s -r %s'% (cfg.BASEDIR,expected_revision)
                cleanup_cmd = 'svn cleanup %s'% cfg.BASEDIR
                output = subprocess.check_output(cleanup_cmd,shell=True) 
                output = subprocess.check_output(cmd,shell=True)

                revision = re.search('revision\s(\d+).', output).group(1)
                msg.code = 'ack'
                msg.data = "<revision>%s</revision>" % revision
                # Create and initialize a restart_command object to be used
                # during do_post_socket_send_actions.
                this.__restart_command = restart_command(this.user)
                this.__restart_command.do_command(msg)

            except Exception, e:
                this.runner.logger.exception(
                 'Unable to run svn update command: "%s"' % cmd)
                msg.code  = 'cer'
                msg.error = 'Error during SVN update.%s' % (repr(e))
        else:
            try:
                import pysvn
            except ImportError, e:
                msg.code = 'cer'
                msg.error = repr(e)
                return

            svn_client = pysvn.Client()
            # Assigning callback asked by pysvn.
            svn_client.callback_ssl_server_trust_prompt = ssl_server_trust_prompt
            svn_client.callback_get_login = svn_credentials # This is not required if we get read permission without login.
            
            svn_client.cleanup(cfg.BASEDIR)
            # If a revision has not been specified update to HEAD.
            if expected_revision is None:
                to_revision = pysvn.Revision(pysvn.opt_revision_kind.head)
            else:
                to_revision = pysvn.Revision(pysvn.opt_revision_kind.number, 
                                         int(expected_revision))
            revision = svn_client.update(cfg.BASEDIR, revision=to_revision)[0].number
            # Negative numbers indicate an SVN update error code.
            if revision < 0:
                msg.code  = 'cer'
                msg.error = 'Error(%d) during SVN update.' % revision
            else:
                msg.code = 'ack'
                msg.data = "<revision>%d</revision>" % revision

                # Create and initialize a restart_command object to be used
                # during do_post_socket_send_actions.
                this.__restart_command = restart_command(this.user)
                this.__restart_command.do_command(msg)

    def do_post_socket_send_actions(this):
        if this.__restart_command:
            # We cannot restart the service properly on most linux platforms.
            # Instead we will just reboot the entire machine.
            if HOST_INFO.isLinux():
                this.runner.notifyRebootRequest('')
                return
            # Actually restart the service runner.
            this.__restart_command.do_post_socket_send_actions()

class restart_command(BaseCommand):
    """Cause the service to exit, and restart.

    This command will attempt to immediatley stop the ServiceRunner service,
    and restart it
    """
    name = 'restart'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])
        this.__restarting = False

    def do_command(this, msg):
        if not this.runner.check_acl(this.user,msg):
            return
        if this.runner.DATA['state'] != this.runner.STATE_IDLE:
            msg.code  = 'cer'
            msg.error = 'Can NOT restart service from state %s' \
                            %this.runner.DATA['state']
        else:
            this.__restarting = True
            msg.code = 'ack'

    def do_post_socket_send_actions(this):
        """quits the code after sending to the server that we are restarting"""
        # If the restart was not allowed, skip this step
        if not this.__restarting:
            return

        if HOST_INFO.isWindows():
            this.runner.logger.info(
                        "RESTART: Killing myself to trigger service restart.")
            os.kill(os.getpid(), 2)
        elif HOST_INFO.isRunningJython() or HOST_INFO.isAndroid():
            # Jython does not support forking so we must try something else.
            # If we execute a service restart command, that script will kill the
            # current process and relaunch us. This only works for machines
            # that were setup using revision 4007 or later, since that is when
            # the restart command was fixed on Ubuntu. All Jython machines
            # will support this.
            #
            # Also run this method for Android (assumed to run on Ubuntu), since
            # the forking approach below has not actually been starting the
            # service again.
            this.runner.logger.info('RESTART: Restarting service with service'+\
                    ' restart command.')
            subprocess.Popen('service %s restart' % cfg.SERVICE_NAME, shell=True)
        else:
            if os.fork() == 0:
                this.runner.logger.info(
                                    'RESTART: Restarting service from child.')
                # We don't fully understand how this works, but it does!
                # The parent process is killed by child and the child is 
                # only killed when the new daemon starts.
                import SvcMgr
                s = SvcMgr.Service(cfg.SERVICE_NAME)
                s.stop(force=True)
                time.sleep(2)
                s.start()
                sys.exit()

class help_command(BaseCommand):
    """Get generic help on all commands or detailed help on a specific command.
    <command>
    If no arguments are passed, this command will return a synopsis of all
    commands that are available.
    If a specific command is requested, then it will return a detailed
    help message for the specific command.
    """

    name = 'help'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,['cmd'])

    def do_command(this, msg):
        xml = "<help>\n"
        all_attrs = globals()

        if this.args['cmd'] is not None:
            # Hanle a request for detailed help on a single command
            cmdname = this.args['cmd'] + '_command'

            # make sure the command actually exists
            if not all_attrs.has_key(cmdname):
                xml += '  ERROR: no such command "%s"\n' %this.args['cmd']
            else:
                doc = all_attrs[cmdname].__doc__.strip().split('\n')
                xml += this.args['cmd'] + " " + doc[1].strip() + "\n\n"
                xml += doc[0] + "\n\n"
                xml += "\n".join( [x[4:] for x in doc[2:]] ) + "\n"
        else:
            # Get basic help on all commands
            keys = all_attrs.keys()
            keys.sort()
            for clsname in keys:
                cls = all_attrs[clsname]
                if not clsname.endswith('_command'):
                    continue
                doc = cls.__doc__.split('\n')
                xml += '%s %s\n    %s\n\n' %(cls.name, doc[1].strip(), doc[0])
        xml += '</help>\n'
        msg.code = 'ack'
        msg.data = xml

class hostinfo_command(BaseCommand):
    """Returns information about the host system

    An xml message, containing the os name, os version, os patch level, and
    architecture (32 or 64 bit) will be returned, eg:
      <hostinfo>
        <system>Windows XP</system>
        <arch>32bit</arch>
        <patch>Service Pack 3</patch>
        <version>5.1.2600</version>
      </hostinfo>
    """
    name = 'hostinfo'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        xml = "<hostinfo>\n" +\
              "  <system>%s</system>\n"     %HOST_INFO.os +\
              "  <arch>%s</arch>\n"         %HOST_INFO.arch +\
              "  <patch>%s</patch>\n"       %HOST_INFO.patch +\
              "  <version>%s</version>\n"   %HOST_INFO.version +\
              "  <hardware>%s</hardware>\n" %HOST_INFO.hardware

        # UID is only available for Android platforms.
        if HOST_INFO.isAndroid():
            xml += "  <uid>%s</uid>\n" % HOST_INFO.uid

        xml += "</hostinfo>\n"

        msg.code = 'ack'
        msg.data = xml

class status_command(BaseCommand):
    """Returns a summary of the current automation state

    This command will return a detailed XML message containing all of the
    available status, state, and statistcs information for the automation
    service.
    """
    name = 'status'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        if getattr(this.runner, 'suite'):
            suiteName = this.runner.suite.name
            productName = this.runner.suite.productName
            productCodename = this.runner.suite.productCodename
            productVersion = this.runner.suite.productVersion
        else:
            suiteName = "None"
            productName = "None"
            productCodename = "None"
            productVersion = "None"

        if this.runner.DATA['activeTestcase'] is None: testcase = 'None'
        else: testcase = this.runner.DATA['activeTestcase'].friendlyName

        elapsed = 0
        if this.runner.DATA['finishTime'] != 0:
            elapsed = int(this.runner.DATA['finishTime'] - this.runner.DATA['startTime'])
        elif this.runner.DATA['startTime'] != 0:
            elapsed = int(time.time() - this.runner.DATA['startTime'])

        status = \
            "<status>\n" +\
            "  <state>%s</state>\n" %this.runner.DATA['state'] + \
            "  <locked>%s</locked>\n" %str(this.runner.DATA['locked']) + \
            "  <executedBy>%s</executedBy>\n" %str(this.runner.DATA['executedBy']) + \
            "  <message><![CDATA[%s]]></message>\n"%this.runner.DATA['state_msg']+\
            "  <suite>%s</suite>\n" %suiteName + \
            "  <product_name><![CDATA[%s]]></product_name>\n" %productName + \
            "  <product_codename><![CDATA[%s]]></product_codename>\n" %productCodename + \
            "  <product_version>%s</product_version>\n" %productVersion + \
            "  <testcase>%s</testcase>\n" %testcase +\
            "  <testCount>%d</testCount>\n" %this.runner.DATA['testCount'] +\
            "  <runCount>%d</runCount>\n" %this.runner.DATA['runCount'] +\
            "  <passCount>%d</passCount>\n" %this.runner.DATA['passCount'] +\
            "  <failCount>%d</failCount>\n" %this.runner.DATA['failCount'] +\
            "  <blockCount>%d</blockCount>\n" %this.runner.DATA['blockCount'] +\
            "  <crashCount>%d</crashCount>\n" %this.runner.DATA['crashCount'] +\
            "  <errorCount>%d</errorCount>\n" %this.runner.DATA['errorCount'] +\
            "  <startTime>%d</startTime>\n" %this.runner.DATA['startTime'] +\
            "  <finishTime>%d</finishTime>\n" %this.runner.DATA['finishTime'] +\
            "  <elapsedTime>%d</elapsedTime>\n" %elapsed +\
            "  <resultState>\n" +\
            str(this.runner.DATA['resultState']) +\
            "  </resultState>\n" +\
            "</status>\n"
        msg.code = 'ack'
        msg.data = status

class uploadstatus_command(BaseCommand):
    """Responds with the current (or last) upload status

    This command will return the status of the last requested results upload
    for the currently loaded test suite.
    """
    name = 'uploadstatus'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        # respond with the status
        msg.code = 'ack'
        msg.data = str(this.runner.DATA['resultState'])

class uploadresults_command(BaseCommand):
    """Instruct the service to upload the current results
    <import_type>
    This command will trigger an upload of all the results for the current
    test suite to the central server (deathstar). All results, including
    logs, statistics, and any attached data files will be copied via FTP and
    checksumed upon copy completion. The results will then be imported into
    the results database by the dashboard service. This process can take a
    very long time for large test suites, and thes status of the upload can
    be checked using the uploadstatus command.

        import_type - a string representing the type of upload this
                      will be: 'pending', 'testing', 'official'
    """
    name = 'uploadresults'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,['import_type'])
        this.ENABLED_ADAPTER = [config._config.TEST_NET_ADAPTER]
        for adapter in this.ENABLED_ADAPTER:
           testnet = NetDevice.NetDevice(adapter)
           testnet.enable()
           testnet.waitForIP(config._config.NETWORKS.Untrusted.Prefix)
           if HOST_INFO.isMac():
              NET_CONFIG.setHighestPriorityService(testnet.servicename)
           if HOST_INFO.isLinux():
              mgmt = NetDevice.NetDevice(config._config.MANAGEMENT_ADAPTER)
              mgmt.disable()
              
           

    def do_command(this, msg):
        """Upload the results for this test suite"""
        if not this.runner.check_acl(this.user,msg):
            return
        if not getattr(this.runner, 'suite') or this.runner.suite is None:
            msg.code  = 'cer'
            msg.error = 'Cannot upload results, no suite loaded!'
            return
        if this.runner.DATA['state'] != this.runner.STATE_SUITE_STOPPED and \
           this.runner.DATA['state'] != this.runner.STATE_SUITE_COMPLETE and \
           this.runner.DATA['state'] != this.runner.STATE_UPLOADING_CODECOVERAGE_COMPLETE:
            msg.code  = 'cer'
            msg.error = 'Cannot upload results, when in state %s' \
                    %this.runner.DATA['state']
            return
        if not this.runner.suite.resultDataEnabled:
            msg.code  = 'cer'
            msg.error = 'Result data is not enabled!'
            return
        if not os.path.exists(this.runner.suite.resultDataDir):
            msg.code  = 'ser'
            msg.error = 'Could not find result data directory: %s' \
                    %this.runner.suite.resultDataDir
            return

        #lock the service so nothing happens to the FTP
        lock_command('admin','').do_command(msg)
        if msg.code != 'ack':
            msg.code  = 'ser'
            msg.error = 'failed to aquire lock of service: %s' %msg.error
            return

        # Set the runner state
        this.runner.DATA['PREVIOUS_STATE'] = this.runner.DATA['state']
        this.runner.setState(this.runner.STATE_UPLOADING_RESULTS)

        # create and start the ftp
        this.runner.FTP_THREAD = ServiceRunnerCore.FTPResults(
                this.runner,this.args['import_type'],this.runner.suite.name)
        this.runner.FTP_THREAD.start()

        # set the state, and respond to the client
        if this.user is None:
            this.runner.DATA['resultState'].uploadedBy = 'Anonymous'
        else:
            this.runner.DATA['resultState'].uploadedBy = this.user
        msg.code = 'ack'
        msg.data = ''

class uploadcodecoverage_command(BaseCommand):
    """Instruct the service to upload MON.txt
    

        import_type - a string representing the type of upload this
                      will be: 'pending', 'testing', 'official'
    """
    name = 'uploadcodecoverage'
    def __init__(this,user,args=None):
        this.user = user
        #this.suitename = this._parse_args(args,['suitname'])
        this.args = this._parse_args(args,['import_type'])

    def do_command(this, msg):
        """Upload the results for this test suite"""
        #if not this.runner.check_acl(this.user,msg):
        #    return
        if not getattr(this.runner, 'suite') or this.runner.suite is None:
            msg.code  = 'cer'
            msg.error = 'Cannot upload codecoverage, no suite loaded!'
            return
        if this.runner.DATA['state'] != this.runner.STATE_SUITE_STOPPED and \
           this.runner.DATA['state'] != this.runner.STATE_SUITE_COMPLETE:
            msg.code  = 'cer'
            msg.error = 'Cannot uploadcodecoverage, when in state %s' \
                    %this.runner.DATA['state']
            return
        # if not this.runner.suite.resultDataEnabled:
            # msg.code  = 'cer'
            # msg.error = 'Result data is not enabled!'
            # return
        # if not os.path.exists(this.runner.suite.resultDataDir):
            # msg.code  = 'ser'
            # msg.error = 'Could not find result data directory: %s' \
                    # %this.runner.suite.resultDataDir
            # return

        #lock the service so nothing happens to the FTP
        #lock_command('admin','').do_command(msg)
        #if msg.code != 'ack':
        #    msg.code  = 'ser'
        #    msg.error = 'failed to aquire lock of service: %s' %msg.error
        #    return

        # Set the runner state
        this.runner.DATA['PREVIOUS_STATE'] = this.runner.DATA['state']
        this.runner.setState(this.runner.STATE_UPLOADING_CODECOVERAGE)
        this.runner.logger.debug('the suitename is %s' %this.args['suitename'])

        # create and start the ftp
        this.runner.FTP_THREAD1 = ServiceRunnerCore.FTPCodeCoverage(
                this.runner,this.args['import_type'],this.args['suitename'])
        this.runner.logger.debug('Going to start the thread')
        
        this.runner.FTP_THREAD1.start()

        # set the state, and respond to the client
        #if this.user is None:
        #    this.runner.DATA['resultState'].uploadedBy = 'Anonymous'
        #else:
        #    this.runner.DATA['resultState'].uploadedBy = this.user
        msg.code = 'ack'
        msg.data = ''

class setuploadresult_command(BaseCommand):
    """Tell the service about the result from a recent request to upload results
    <id> <err>
    This method is typically called by the dashboard_service to set the
    the id of recently uploaded result sets. It takes two arguments,
    one of which is:

        id  - The numerid ID of the results set in the database

        err - This should be string representation of the error encountered
              trying to import the results. If there was no error, this
              should be set to None
    """
    name = 'setuploadresult'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,['id','error'])

    def do_command(this, msg):
        if this.args['id'] is None:
            this.runner.DATA['resultState'].resultSetId = None
        else:
            this.runner.DATA['resultState'].uploadState = \
                                    ServiceRunnerCore.FTPResults.STATE_SUCCESS
            this.runner.DATA['resultState'].resultSetId = this.args['id']
            this.runner.logger.info('Automation Results successfully' \
            + ' imported into database with id: %s' %this.args['id'])

        if this.args['error'] is None:
            this.runner.DATA['resultState'].uploadError = None
        else:
            this.runner.DATA['resultState'].uploadState = \
                                    ServiceRunnerCore.FTPResults.STATE_FAILURE
            this.runner.DATA['resultState'].uploadError = this.args['error']
            this.runner.logger.error('Automation Results failed imported'\
            + ' into database with error: %s' %this.args['error'])

        this.runner.saveState()

        msg.code = 'ack'
        msg.data = "<id>%s</id>" %this.args['id'] +\
                   "<err>%s</err>" %this.args['err']

class start_command(BaseCommand):
    """Start automation on the currently loaded test suite

    This command will start a full automation run on whatever test suite is
    currently loaded. If not suite is loaded, it will trigger an error
    """
    name = 'start'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        """This method will execute the currently loaded test suite.
        To be able to run a test suite, the runner MUST be in the state
        STATE_SUITE_LOADED. If the runner is in any other state, this
        method will do nothing.
        """
        if not this.runner.check_acl(this.user,msg):
            return
        if this.runner.DATA['state'] != this.runner.STATE_SUITE_LOADED:
            msg.code  = 'cer'
            msg.error = \
                    'Can NOT run a test suite from state %s' %this.runner.DATA['state']
        else:
            this.runner.DATA['executedBy'] = this.user
            this.runner.setState(this.runner.STATE_SUITE_STARTING)
            this.runner.start_testing()
            msg.code = 'ack'

class stop_command(BaseCommand):
    """Stop automation on the currently running test suite

    This command will stop any currently running automation, either a full
    run, or a subset run. If a full run is stopped, it can be resumed
    using the resume command.
    """
    name = 'stop'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        """This method will stop the currently running tests. If there
        is no running tests, this method will return a client error."""
        if not this.runner.check_acl(this.user,msg):
            return
        if this.runner.DATA['state'] == this.runner.STATE_SUITE_RUNNING or \
           this.runner.DATA['state'] == this.runner.STATE_SUBSET_RUNNING:
            if this.runner.DATA['state'] == this.runner.STATE_SUBSET_RUNNING:
                this.runner.STOPPED_DURING_SUBSET = True
            this.runner.setState(this.runner.STATE_SUITE_STOPPING)
            this.runner.stopTesting()
            msg.code = 'ack'
        else:
            msg.code  = 'cer'
            msg.error = \
                'Can NOT stop a test suite from state %s' %this.runner.DATA['state']

class resume_command(BaseCommand):
    """Resume the currently stopped test suite

    This command will resume automation that has been stopped using
    the stop command, from the point it was stopped, and continue till all
    tests have been executed.
    """
    name = 'resume'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        """This method will resume the currently loaded test suite.
        To be able to resume a test suite, the runner MUST be in the state
        STATE_SUITE_STOPPED. If the runner is in any other state, this
        method will do nothing.
        """
        if not this.runner.check_acl(this.user,msg):
            return
        if this.runner.DATA['state'] != this.runner.STATE_SUITE_STOPPED:
            msg.code  = 'cer'
            msg.error = 'Can NOT resume a test suite from state %s' \
                            %this.runner.DATA['state']
        else:
            this.runner.resume_testing()
            msg.code = 'ack'

class reload_command(BaseCommand):
    """Relaod the currently loaded testsuite

    This command would be used to trigger a reload of the currently loaded
    test suite. This could be useful to pick up changes in any of the
    test cases or support libraries
    """
    name = 'reload'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        if not this.runner.check_acl(this.user,msg):
            return
        module_to_reload = this.runner.DATA['suiteModule']
        this.runner.setState(this.runner.STATE_SUITE_LOADING)
        if module_to_reload is None:
            this.runner.setState(this.runner.STATE_SUITE_LOAD_FAIL)
            this.runner.DATA['state_msg'] = \
                    "Attempt to reload when there is no test suite loaded"
            msg.code  = 'cer'
            msg.error = this.runner.DATA['state_msg']
        else:
            this.runner.load(module_to_reload)
            msg.code = 'ack'

class goidle_command(BaseCommand):
    """Instruct the automation service to go to an idle state

    This command causes the automation service to reset itself to an idle
    state, allowing the service to be used to execute new tests.
    """
    name = 'goidle'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        if not this.runner.check_acl(this.user,msg):
            return

        if this.runner.DATA['state'] not in [
                this.runner.STATE_IDLE,
                this.runner.STATE_SUITE_LOADED,
                this.runner.STATE_SUITE_LOAD_FAIL,
                this.runner.STATE_SUITE_STOPPED,
                this.runner.STATE_SUITE_COMPLETE,
                this.runner.STATE_SUITE_ERRORED,
                this.runner.STATE_KICKSTART_ERROR,
                this.runner.STATE_MAINTENANCE_ERROR]:
            msg.code  = 'cer'
            msg.error = \
                'Can NOT go idle from state %s' %this.runner.DATA['state']
            return

        msg.code = 'ack'
        this.runner.suite = None
        this.runner.DATA['state_msg'] = 'idle'
        this.runner._initialize_data()
        this.runner.setState(this.runner.STATE_IDLE)

class getcases_command(BaseCommand):
    """Retrieves a list of all test cases from the currently loaded suite

    This command will return a list of all of the testcases in the current
    suite. Each test case in the list will include the full name, description,
    and status of the testcase.
    """
    name = 'getcases'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        msg.code  = 'ack'
        if this.runner.suite is not None:
            msg.code  = 'ack'
            xml = ''
            for testcase in this.runner.suite:
                if testcase.enabled is False: # skip disabled test cases
                    continue
                xml+='<testcase>\n'
                xml+='<name>%s</name>\n' %testcase.friendlyName
                xml+='<description><![CDATA[%s]]></description>\n' %testcase.description
                xml+='<status>%s</status>\n' %testcase.result.upper()
                xml+='</testcase>\n'
            msg.data = xml

class getsuites_command(BaseCommand):
    """Retrieves a list of all the tests available on this system

    This command will return a list of all the test suites and test collections
    that this system has installed in it's automation directory.
    """
    name = 'getsuites'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        testsuites = {}
        results = os.listdir(cfg.TESTSUITE_DIR)
        results.sort()
        for directory in results:
            pyfiles = []
            # if this is a SVN directory
            if directory.startswith('.'):
                continue
            # if this path is not a directory, skip it
            if not os.path.isdir(os.path.join(cfg.TESTSUITE_DIR, directory)):
                continue
            files = os.listdir(os.path.join(cfg.TESTSUITE_DIR, directory))
            files.sort()
            for file in files:
                (basename, ext) = os.path.splitext(file)
                if ext.lower() == '.py':
                    pyfiles.append(basename)

            testsuites[directory] = pyfiles
        tests = "<testsuites>\n"
        for product in testsuites.keys():
            tests += "<folder>\n<product>" + product + "</product>\n<suites>"
            tests += ','.join(testsuites[product])
            tests += "</suites>\n</folder>\n"

        tests += "</testsuites>\n"

        testcases = {}
        results = os.listdir(cfg.TESTCASE_DIR)
        results.sort()
        for directory in results:
            pyfiles = []
            # if this is a SVN directory
            if directory.startswith('.'):
                continue
            # if this path is not a directory, skip it
            if not os.path.isdir(os.path.join(cfg.TESTCASE_DIR, directory)):
                continue
            files = os.listdir(os.path.join(cfg.TESTCASE_DIR, directory))
            files.sort()
            for file in files:
                (basename, ext) = os.path.splitext(file)
                if ext.lower() == '.py':
                    pyfiles.append(basename)

            testcases[directory] = pyfiles
        tests += "<testcases>\n"
        for product in testcases.keys():
            tests += "<folder>\n<product>" + product + "</product>\n<cases>"
            tests += ','.join(testcases[product])
            tests += "</cases>\n</folder>\n"

        tests += "</testcases>\n"

        msg.code = 'ack'
        msg.data = tests

class getsaves_command(BaseCommand):
    """Retrieve a list and details of all the save states available

    This command will return a summary list and detailed list of all the
    saved automation states available on the current system.
    """
    name = 'getsaves'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        saves = []
        savesDetail = "<details>\n"
        summaryData = []
        # check for non existant results dir
        try:
            dirs = os.listdir(cfg.RESULTS_DIR)
            dirs.sort()
        except Exception, exc:
            this.runner.logger.exception(exc)
            msg.code = 'ack'
            msg.data = '<saves></saves>\n'
            return

        for dir in dirs:
            save_file = os.sep.join([cfg.RESULTS_DIR,dir,'save_state.pickle'])
            summary_file = os.sep.join([cfg.RESULTS_DIR,dir,'summary.pickle'])
            if os.path.isfile(save_file) and os.path.isfile(summary_file):
                saves.append(dir)
                savesDetail += "  <save name=\"" + dir + "\">\n"
                savesDetail += "    <name>" + dir +"</name>\n"
                try:
                    with open(summary_file, 'rb') as summaryPickle:
                        summaryData = cPickle.load(summaryPickle)
                except Exception,exc:
                    this.runner.logger.exception(exc)
                if len(summaryData) > 0:
                    for key in summaryData.keys():
                        savesDetail += "<%s><![CDATA[%s]]></%s>" \
                                %(key, summaryData[key], key)
                savesDetail += "  </save>\n"
        savesDetail +=("</details>\n")
        msg.code = 'ack'
        msg.data = "<saves>"+",".join(saves)+"</saves>\n"
        msg.data += savesDetail

class lock_command(BaseCommand):
    """Lock this host, so no other user can manipulate it

    This command will lock the host, so that only the person who locked it
    can perform actions on the host (any user can still get status from the
    host). Only the user holding the lock will be allowed to unlock the host.
    """
    name = 'lock'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        # check if we're already locked
        if this.runner.DATA['locked'] is not False:
            # if the user is admin, it can "take over" the lock
            if this.user == 'admin':
                # remember the user who previously held the lock
                this.runner.PRE_ADMIN_LOCKED = this.runner.DATA['locked']
                this.runner.logger.info(
                        "admin user has preempted lock from user %s"\
                        %this.runner.PRE_ADMIN_LOCKED)

            # if a different user tries to take the lock, reject them
            elif this.runner.DATA['locked'] != this.user:
                msg.code  = 'cer'
                msg.error = "The service is already locked by '%s'" \
                        %this.runner.DATA['locked']
                this.runner.logger.info(
                        "lock attempt rejected for user %s " %this.user + \
                        "because service is already locked by user %s"
                        %this.runner.DATA['locked'])
                return

        this.runner.logger.info("Locked service on behalf of user %s"%this.user)
        this.runner.DATA['locked'] = this.user
        msg.code = 'ack'
        msg.data = "<locked>%s</locked>\n" %this.runner.DATA['locked']
        return

class unlock_command(BaseCommand):
    """Release the lock held on this host

    This command will release a previous lock held by the same user for
    this host. If a different user attempts to unlock the host an error
    will be returned and the host will remain locked.
    """
    name = 'unlock'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        # check if we're already unlocked
        if this.runner.DATA['locked'] is False:
            msg.code = 'ack'
            msg.data = "<locked>%s</locked>\n" %this.runner.DATA['locked']
            return

        if this.user == 'admin':
            if this.runner.DATA['locked'] == 'admin':
                # restore the user who previously held the lock
                this.runner.logger.info(
                        "Releasing admin lock, and restoring lock to user %s" \
                        %(this.runner.PRE_ADMIN_LOCKED))
                this.runner.DATA['locked'] = this.runner.PRE_ADMIN_LOCKED
                this.runner.PRE_ADMIN_LOCKED = False
            else:
                # the admin can also force an unlock
                this.runner.DATA['locked'] = False
                this.runner.logger.info(
                        "Unlocked service on behalf of user %s" %this.user)
        elif this.runner.DATA['locked'] == this.user:
            # the same user requested the unlock, who held the lock.
            this.runner.DATA['locked'] = False
            this.runner.logger.info(
                    "Unlocked service on behalf of user %s" %this.user)
        else:
            # This is not the user who set the lock, reject them!
            msg.code  = 'cer'
            msg.error = "Permission denied! The service is locked by '%s'" \
                        %this.runner.DATA['locked']
            this.runner.logger.info(
                        "unlock attempt rejected for user %s " %this.user + \
                        "because service is locked by user %s"
                        %this.runner.DATA['locked'])
            return

        msg.code = 'ack'
        msg.data = "<locked>%s</locked>\n" %this.runner.DATA['locked']
        return

class getfile_command(BaseCommand):
    """Retrieve a specific file associated with a specific test case
    <testcase> <filename>
    This command will retrieve and return the requested file from the
    requested test case. This command takes two arguments:

       testcase - This is the name of the testcase which holds the file

       filename - the specific file name you wishe to retrieve
    """
    name = 'getfile'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,['testcase','filename'])

    def do_command(this, msg):
        if this.runner.suite is None:
            msg.code  = 'cer'
            msg.error = 'Cannot get testcase log, no suite is loaded'
        else:
            testcase = this.runner.suite.findTestCase(this.args['testcase'])
            datafile = None
            for pair in testcase.resultData:
                if pair[1] == this.args['filename']:
                    datafile = pair[0]
                    break
            if datafile is None:
                msg.code = 'cer'
                msg.error = 'testcase "%s" has not result file named "%s"' \
                        %(this.args['testcase'], this.args['filename'])
                return
            fp = open(datafile, 'rb')
            data = fp.readlines()
            fp.close()
            # Base64 encode the binary file content so it can be transmitted
            # over XML reliably.
            data = base64.encodestring("".join(data))
            msg.code = 'ack'
            msg.data = "<file><![CDATA[%s]]></file>" % data

class loadstate_command(BaseCommand):
    """Instruct the service to load a specific saved state
    <statename>
    This command will load a previous save state into the current automation
    service. This command takes just one argument:

        statename - The name of the saved state file (as returned from getsaves)
    """
    name = 'loadstate'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,['statename'])

    def do_command(this, msg):
        if not this.runner.check_acl(this.user,msg):
            return

        if this.runner.DATA['state'] not in [
                this.runner.STATE_IDLE,
                this.runner.STATE_SUITE_LOADED,
                this.runner.STATE_SUITE_LOAD_FAIL,
                this.runner.STATE_SUITE_STOPPED,
                this.runner.STATE_SUITE_COMPLETE,
                this.runner.STATE_SUITE_ERRORED ]:
            msg.code  = 'cer'
            msg.error = 'Can NOT load saved suite from state %s' \
                            %this.runner.DATA['state']
        else:
            state_file = os.path.sep.join(\
                    ['results', this.args['statename'], 'save_state.pickle'])
            if not os.path.exists(state_file):
                msg.code = 'cer'
                msg.error = "No save state found for test run '%s'" %dir
                return
            val = this.runner.loadState(state_file)
            if val is True:
                msg.code = 'ack'
            else:
                msg.code  = 'ser'
                msg.error = val.message

class load_command(BaseCommand):
    """Instruct the service to load a specific test suite or collection
    <suite>
    This command will load a new test suite or test collection in to the
    service. This command takes just one argument:

        suite - The name of the suite file (as returned from getsuites)
    """
    name = 'load'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,['suite'])

    def do_command(this, msg):
        if not this.runner.check_acl(this.user,msg):
            return

        if this.runner.DATA['state'] not in [
                this.runner.STATE_IDLE,
                this.runner.STATE_SUITE_LOADED,
                this.runner.STATE_SUITE_LOAD_FAIL,
                this.runner.STATE_SUITE_STOPPED,
                this.runner.STATE_SUITE_COMPLETE,
                this.runner.STATE_SUITE_ERRORED,
                this.runner.STATE_KICKSTART ]:
            msg.code  = 'cer'
            msg.error = \
                'Can NOT load new test suite from state %s' %this.runner.DATA['state']
        else:
            this.runner.logger.info('Inside load method')
            this.runner.setState(this.runner.STATE_SUITE_LOADING)
            this.runner.DATA['suiteModule'] = this.args['suite']
            (rv,err) = this.runner.load(this.runner.DATA['suiteModule'])
            if rv is True:
                if 'build' in this.args.keys():
                    this.runner.suite.build = this.args['build']
                msg.code = 'ack'
            else:
                msg.code  = 'ser'
                msg.error = err

class caseinfo_command(BaseCommand):
    """Retrive detailed information about a specific testcase
    <testcase>
    This command will retrieve all the detailed information about a specific
    test case in the currently loaded suite. This command takes one argument:

        testcase - The name of the testcase (as returned from getcases)
    """
    name = 'caseinfo'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,['testcase'])

    def do_command(this, msg):
        if this.runner.suite is None:
            msg.code  = 'cer'
            msg.error = 'Cannot get testcase log, no suite is loaded'
        else:
            msg.code  = 'ack'
            xml = ''
            testcase = this.runner.suite.findTestCase(this.args['testcase'])

            # clean up the docstring a bit
            if testcase.longdesc is None:
                docstring = ''
            else:
                docstring = testcase.longdesc.replace('        ','')
                docstring = docstring.replace('@DESCRIPTION', '\n@DESCRIPTION')

            xml += '<testcase>\n'
            xml += '<name>%s</name>\n' %testcase.friendlyName
            xml += '<description><![CDATA[%s]]></description>\n' %testcase.description
            xml += '<startTime>%d</startTime>\n' %testcase.startTime
            xml += '<finishTime>%d</finishTime>\n' %testcase.finishTime
            xml += '<status>%s</status>\n' %testcase.result.upper()
            for log in testcase.log:
                xml += '<log><time>%s</time>' %log[0] + \
                       '<message><![CDATA[%s]]></message></log>\n' %log[1]
            xml += '<documentation><![CDATA[%s]]></documentation>\n' %docstring
            for data in sorted(testcase.resultData, 
                               key=lambda i: str(i[1]).lower()):
                xml += "<resultData>%s</resultData>\n" %data[1]
            xml += '</testcase>\n'
            msg.data = xml

class runsubset_command(BaseCommand):
    """Instruct the service to run a subset of the loaded test suite
    <testcases>
    This command will instruct the service to execute a particular selection
    of the currently loaded test cases. The test cases will run in the order
    recieved by this command, and can be interupted by the stop command. This
    command takes one argument:

        testcases - a comma separted list of test case names
    """
    name = 'runsubset'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,['testcases'])

    def do_command(this, msg):
        """Execute (possibly re-executing) a subset of the test suite"""
        if not this.runner.check_acl(this.user,msg):
            return
        if this.runner.DATA['state'] != this.runner.STATE_SUITE_LOADED and \
           this.runner.DATA['state'] != this.runner.STATE_SUITE_COMPLETE and \
           this.runner.DATA['state'] != this.runner.STATE_SUITE_STOPPED:
            msg.code  = 'cer'
            msg.error = 'Can NOT run a testcase from state %s' \
                            %this.runner.DATA['state']
        else:
            # get a list of all the testcase objects and make sure they are
            # real testcases (they exist in this suite). If they don't exist,
            # return a client error.
            testcase_list = []
            casenames = this.args['testcases'].split(",")
            for casename in casenames:
                try:
                    testcase_list.append( \
                    this.runner.suite.findTestCase(casename) )
                except AttributeError, exc:
                    this.runner.logger.exception(exc)
                    msg.code  = 'cer'
                    msg.error = str(exc)
                    return

            # Remember the subset run list, so we can adjust counters correctly
            this.runner.DATA['SUBSET_RUN_LIST'] = \
                    ServiceRunnerCore.SubsetList(testcase_list)

            # set the state, and do it.
            this.runner.DATA['PREVIOUS_STATE'] = this.runner.DATA['state']
            this.runner.setState(this.runner.STATE_SUBSET_RUNNING)
            this.runner.subset_testing(testcase_list)
            msg.code = 'ack'

class error_command(BaseCommand):
    """Trigger an immediate exception in the service - for debugging purposes

    This command is only useful in debugging the command/socket interface
    of the automation service. When this command is execute an Exception
    will be raised immediatley with the error "Server side error".
    """
    name = 'error'
    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        raise Exception("Server side error")

class kickstart_command(BaseCommand):
    """Instruct the service to clean, install, and start automation on a product
    <suite> <install_urls> <uninstalls>
    Kickstart a test for the service. This allows you to install a new
    build, and start a new testsutie all in one command. This could cause
    a reboot if the installer requires a reboot after install or uninstall.
    This command takes 3 arguments

        suite        - the name of the test suite to load and start. If no
                       suite should be loaded, set this to None.

        install_urls - a double caret "^^" separated list of urls to
                       installers that will be installed. If no installers are
                       to be used to this to None.

        uninstalls   - a double caret "^^" separated list of products to
                       uninstall, before the install. If nothing should be
                       uninstalled, set this to None
    """
    name = 'kickstart'

    def __init__(this, user, args=None):
        this.user  = user
        this.args  = args
        this.args = this._parse_args(args,['suite','install_urls','uninstalls'])

    def do_command(this, msg):

        if not this.runner.check_acl(this.user, msg):
            return

        this.runner.DATA['state_msg'] = "Initializing Kickstart"

        this.runner.setState(this.runner.STATE_KICKSTART)

        kickstart = Kickstart.Kickstart(this.user, this.args, this.runner)
        try:
            kickstart.verify_before_start()
            kickstart.start()
            msg.code = 'ack'

        except Exception, e:
            kickstart.set_error(e)

class insanity_command(BaseCommand):
    """Instruct automation to start a Sanity test
    <suite> <currentbuild> <previousbuild>
    This command will cause the automation to start a full sanity run on the
    provided current build. This command takes 3 arguments:

        suite         - the name of the test suite to load and start

        currentbuild  - the build number to be tested

        previousbuild - the build number to be used for upgrade testing
        
        compliance_module - the build number to be used for validating that test cases are run for the same build as requested       
    """
    name = 'insanity'
    def __init__(this,user,args=None):
        this.user  = user
        this.args  = args
        this.args = this._parse_args(args,
                ['suite','currentbuild','previousbuild', 'compliance_module'])

    def do_command(this, msg):
        if msg == 'None': # after a reboot msg would be the string None
            msg = ServiceRunnerCore.ResponseMsg('ack','kickstart')

        if not this.runner.check_acl(this.user,msg):
            return

        # log messages
        this.runner.logger.info("insanity: Load and run %s for version %s" \
                                %(this.args['suite'],this.args['currentbuild']))

        # do it
        load_command(this.user, this.args['suite']).do_command(msg)
        this.runner.suite.build = this.args['currentbuild']
        this.runner.suite.previousBuild = this.args['previousbuild']
        this.runner.suite.compliance_module = this.args['compliance_module']
        
        start_command(this.user).do_command(msg)

        msg.code = 'ack'

class takesnapshot_command(BaseCommand):
    """Instruct the service to take a VM snapshot (only works on VM's)
    <snapshotname>
    This command will trigger the service to take a snapshot of the currently
    running system. This command will only have any useful affect if the system
    is a virtual machine. This command takes one argument:

        snapshotname - the name the snapshot should recieve
    """
    name = 'takesnapshot'

    def __init__(this, user, args='base'):
        this.user  = user
        this.args = this._parse_args(args,['snapshotname'])

    def do_command(this, msg):

        if not this.runner.check_acl(this.user, msg):
            return

        this.runner.DATA['state_msg'] = "Taking Snapshot"

        if this.runner.DATA['state'] is not this.runner.STATE_IDLE:
            msg.code  = 'cer'
            msg.error = "Can NOT take snapshot while in state %s" \
               % this.runner.DATA['state']
        else:
            this.runner.logger.info("Taking snapshot named '%s'." \
                    %this.args['snapshotname'])
            try:
                thread.start_new_thread(
                    this.__take_latest_snapshot, (this.args['snapshotname'],))
                this.runner.setState(this.runner.STATE_REBOOTING)
                msg.code = 'ack'
            except Exception, exc:
                msg.code  = 'cer'
                msg.error = "Failure while attempting to take snapshot! %s" \
                   % repr(exc)
        this.runner.logger.info("Snapshotting completed.")

    def __take_latest_snapshot(this, name):
        time.sleep(5) # Sleeping 5 seconds to ensure the responce is sent.
        import VMManager
        vmManager = VMManager.VMManager()
        vmManager.takeSnapshot(name)

class reverttolatestsnapshotofname_command(BaseCommand):
    """Instruct the service to revert to a specific snapshot (only works on VMs)
    <snapshotname>
    This command will trigger the service to revert to the specified snapshot.
    This command will only have any useful affect if the system
    is a virtual machine. This command takes one argument:

        snapshotname - the name the snapshot should recieve
    """
    name = 'reverttolatestsnapshotofname'

    def __init__(this, user, args=None):
        this.user  = user
        this.args = this._parse_args(args,['snapshotname'])

    def do_command(this, msg):

        if not this.runner.check_acl(this.user, msg):
            return

        this.runner.DATA['state_msg'] = "Reverting To Latest Snapshot of Name"

        if this.runner.DATA['state'] is not this.runner.STATE_IDLE:
            msg.code  = 'cer'
            msg.error = "Can NOT revert snapshot while in state %s" \
               % this.runner.DATA['state']
        else:
            this.runner.logger.info("Reverting to latest snapshot of name '%s'."
                                    % this.args['snapshotname'])
            try:
                thread.start_new_thread(this.__revert_latest_snapshot_of_name,
                                        (this.args['snapshotname'],))
                this.runner.setState(this.runner.STATE_REBOOTING)
                msg.code = 'ack'
            except Exception, exc:
                msg.code  = 'cer'
                msg.error = "Failure while attempting to revert snapshot! %s" \
                   % repr(exc)
        this.runner.logger.info("Snapshot reverting completed.")

    def __revert_latest_snapshot_of_name(this, name):
        time.sleep(5) # Sleeping 5 seconds to ensure the responce is sent.
        import VMManager
        vmManager = VMManager.VMManager()
        vmManager.revertToLatestSnapshot(name)

class svnstatus_command(BaseCommand):
    """Returns SVN revision number and a list of modified files

    This command will get the svn revision of the current working directory.
    If pysvn is installed, this command will also return a list of files
    that have been modified with their status.
    """
    name = 'svnstatus'

    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):
        # Grab the revision number.
        revision_xml = "<revision>%s</revision>\n" % this.__get_revision()
        # Build response message.
        msg.code = 'ack'
        msg.data = "<svnstatus>\n%s%s</svnstatus>" % (revision_xml,
                                                      this.__get_changes_xml())

    def __get_changes_xml(this):
        """Returns an xml that represents all the changes found in
        local repository. If pysvn is not installed, and emptry string
        is returned."""
        try:
            import pysvn
            file_xml = '<file status="%s">%s</file>\n'
            client = pysvn.Client()
            # Assigning callback asked by pysvn.
            client.callback_ssl_server_trust_prompt = ssl_server_trust_prompt
            client.callback_get_login = svn_credentials # This is not required if we get read permission without login.
    
            ignored_status = [pysvn.wc_status_kind.normal,
                              pysvn.wc_status_kind.ignored]

            modified_files = [(str(file['text_status']), str(file['path']))
                              for file in client.status('.')
                              if file['text_status'] not in ignored_status]

            changes_xml = ''
            for status, path in modified_files:
                changes_xml += file_xml % (status, path)
            return changes_xml
        except Exception:
            this.runner.logger.exception('Failed to get SVN modifications')
            return ''

    def __get_revision(this):
        """"This function attempts to find the SVN revision number. It uses
        a few different methods to guarantee a high success rate of
        discovery."""

        # Optimally, pysvn is installed on every machine.
        try:
            this.runner.logger.debug('Attempting to get SVN revision with pysvn')
            import pysvn
            client = pysvn.Client()
            # Assigning callback asked by pysvn.
            client.callback_ssl_server_trust_prompt = ssl_server_trust_prompt
            client.callback_get_login = svn_credentials # This is not required if we get read permission without login.
    
            return str(client.info('.').revision.number)
        except Exception:
            this.runner.logger.exception('Failed to get SVN revision with pysvn')

        # Check the SVN entries file. This may not work with SVN 1.7 or higher.
        try:
            this.runner.logger.debug(
                'Attempting to get SVN revision in entries file')
            entries_path = os.path.join('.svn', 'entries')
            lines = open(entries_path).readlines()
            return lines[3].strip()
        except Exception:
            this.runner.logger.exception(
                'Failed to find SVN revision in entries file')

        if HOST_INFO.isWindows():
            # Windows platforms may have tortoiseSVN installed. If so there will
            # be a database we can check.
            try:
                this.runner.logger.debug(
                    'Attempting to get SVN revision in tortoiseSVN database')
                import sqlite3
                tortoise_svn_db = os.path.join('.svn', 'wc.db')
                conn = sqlite3.connect(tortoise_svn_db)
                sql = 'SELECT revision FROM NODES LIMIT 1'
                return conn.execute(sql).fetchone()[0]
            except Exception:
                this.runner.logger.exception(
                    'Failed to find SVN revision in tortoiseSVN database')
        else:
            # Unix platforms should have the svn command line utility. If so we
            # can just do an "svn info" command on anything in the repo. Use the
            # current directory since __file__ may refer to a .pyc or $py.class
            # file.
            try:
                this.runner.logger.debug(
                    'Attempting to get SVN revision from command line')
                cmd = 'svn info %s' % os.path.dirname(__file__)
                output = subprocess.check_output(cmd, shell=True)
                return re.search('Revision:\s(\d+)', output).group(1)
            except Exception:
                this.runner.logger.exception(
                    'Failed to get SVN revision from command: "%s"' % cmd)

        return 'Unknown'

class cleanup_command(BaseCommand):
    """Cleans up the installers and results folder

    This command will remove all the folders and files inside of the installers
    and results folders under the automation directory.
    """
    name = 'cleanup'

    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):

        try:
            this.__cleanupdir('installers')
            this.__cleanupdir('results')
        except Exception, exc:
            msg.code  = 'cer'
            msg.error = "Failure while attempting to delete directories! %s" \
                   % repr(exc)
            return

        msg.code = 'ack'
        msg.data = 'Cleanup completed \n'

    def __cleanupdir(this, dirname):
        """"This function deletes all the content on a given sub directory in
        the given working directory.
        """
        import shutil

        dir_path = os.path.join(os.getcwd(), dirname)

        if os.path.exists(dir_path):
            for file in os.listdir(dir_path):
                file_path = os.path.join(dir_path, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)

class osupdate_command(BaseCommand):
    """Performs critical updates on the host operating system.

    This command may restart the machine based on the updates that get installed.
    """

    name = 'osupdate'

    def __init__(this, user, args=None):
        this.user = user
        this.args = this._parse_args(args,[])

    def do_command(this, msg):

        if this.runner.DATA['state'] != this.runner.STATE_IDLE:
            msg.code  = 'cer'
            msg.error = 'Can NOT run osupdate from state %s' % \
                        this.runner.DATA['state']
            return

        if not HOST_INFO.isWindows():
            msg.code = 'cer'
            msg.error = 'Cannot perform updates on a non-Windows host'
            return

        this.runner.setState(this.runner.STATE_MAINTENANCE)
        msg.code = 'ack'

    def do_post_socket_send_actions(this):
        if not HOST_INFO.isWindows():
            return

        try:
            import WindowsUpdateAPI

            wu = WindowsUpdateAPI.WindowsUpdater()
            this.runner.DATA['state_msg'] = 'Searching for OS updates'
            search_cnt = wu.search_for_updates(severity=wu.CRITICAL)
            this.runner.logger.info('Found %d OS updates to perform'
                                    % search_cnt)

            if search_cnt == 0:
                this.runner.setState(this.runner.STATE_IDLE)
                this.runner.DATA['state_msg'] = ''
                return

            this.runner.DATA['state_msg'] = 'Downloading %d OS updates' \
                                            % search_cnt
            download_cnt = wu.download_updates()
            this.runner.logger.info('Downloaded %d OS updates'
                                    % download_cnt)

            if download_cnt == 0:
                this.runner.DATA['state_msg'] = 'Failed to download all OS updates'
                this.runner.setState(this.runner.STATE_MAINTENANCE_ERROR)
                return

            this.runner.DATA['state_msg'] = 'Installing %d OS updates' \
                                            % download_cnt
            wu.install_updates()
            this.runner.logger.info('%d OS updates successfully installed'
                                    % wu.pass_count)

            if wu.fail_count > 0:
                this.runner.logger.info('%d OS updates failed to install'
                                        % wu.fail_count)
                this.runner.DATA['state_msg'] = 'Failed to install %d ' + \
                                                'OS updates' % wu.fail_count
                this.runner.setState(this.runner.STATE_MAINTENANCE_ERROR)

            if wu.reboot_required:
                this.runner.logger.info('Reboot required after OS updates')
                this.runner.notifyRebootRequest("")
            else:
                this.runner.logger.info('No reboot required')
                this.runner.setState(this.runner.STATE_IDLE)
                this.runner.DATA['state_msg'] = ''

        except Exception, exc:
            this.runner.logger.exception(exc)
            this.runner.DATA['state_msg'] = 'Failed to update the OS: %s' \
                                            % repr(exc)
            this.runner.setState(this.runner.STATE_MAINTENANCE_ERROR)

class getdevcode_command(BaseCommand):
    """Download and replace local code with files found in user's dev location
    <dev_name>
    This command will download all the file found in the user's dev location.
    This command takes one optional argument:

        dev_name - your dev name, usually your cec username

    If dev_name is not given, your CEC username will be used.
    """
    name = 'getdevcode'

    # Settings
    CODE_ROOT_DIR     = os.getcwd()
    FTP_SERVER        = 'deathstar.cisco.com'
    FTP_USERNAME      = 'autobot'
    FTP_PASSWORD      = 'pooh'
    FTP_ROOT_DIR_BASE = '/disk/share/devcode/%s/'

    def __init__(this,user,args=None):
        this.user = user
        this.args = this._parse_args(args,[])
        this.files_copied = []

    def do_command(this, msg):

        # If dev name was not passed in, use username.
        dev_name = this.args.get('dev_name') or this.user
        this.ftp_root_dir = this.FTP_ROOT_DIR_BASE % dev_name
        # Download all the filess for this developer
        this.download_files(dev_name)

        if len(this.files_copied) > 0:
            xml = "<dev_name>%s</dev_name>" % dev_name
            xml += "<files>\n"
            for f in this.files_copied:
                xml += "<file>%s</file>\n" % f
            xml += "</files>\n"

            msg.code = 'ack'
            msg.data = xml

        else:
            msg.code  = 'cer'
            msg.error = 'No files found in %s:%s' % (this.FTP_SERVER,
                                                     this.ftp_root_dir)

    def download_files(this, dev_name):
        # Recursively downloads files/folders
        ftp = ftplib.FTP(this.FTP_SERVER)
        try:
            ftp.login(this.FTP_USERNAME, this.FTP_PASSWORD)
            this.copy_folder(ftp, this.ftp_root_dir)
        finally:
            ftp.quit()

    def is_ftp_file(this, ftp, f):
            try:
                ftp.size(f)
            except ftplib.error_perm:
                return False
            return True

    def copy_folder(this, ftp, folder):
        for item in ftp.nlst(folder):
            parsed_item = item.replace(this.ftp_root_dir, '')
            if os.sep != '/':
                parsed_item = parsed_item.replace('/', '\\')
            parsed_item = os.path.join(this.CODE_ROOT_DIR, parsed_item)
            if this.is_ftp_file(ftp, item):
                ftp.retrbinary('RETR ' + item, open(parsed_item, 'wb').write)
                this.files_copied.append(parsed_item)
            else:
                if not os.path.exists(parsed_item):
                    os.makedirs(parsed_item)
                this.copy_folder(ftp, item)
