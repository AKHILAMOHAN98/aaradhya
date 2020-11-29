import socket
import time
import struct
import xml.etree.ElementTree as etree
import subprocess
import re
import os
import logging
import json

# DO NOT instantiate a global instance of HostInfo here!!
# If you do, Android Automation will implode due to circular imports between
# HostInfo and AndroidManager. If any calls to HostInfo must be made, use
# HostInfo.HostInfo() instead.
import HostInfo

class AdbParseError(Exception): pass
class AdbError(Exception): pass
class AdbTimeout(Exception): pass
class SystemCommandException(Exception): pass

LOGGER = logging.getLogger("automation")

################################################################################
#
# Utility Functions
#
################################################################################
def parseProperties(text):
    """
    Parses the output of the "adb shell getprop" command. Returns a dictionary
    of properties to values.
    """
    props = dict()
    # All the properties are formatted at such:
    # [net.dns.search]: [cisco.com]
    regex = re.compile('\[(.+)\]: \[(.*)\]')
    matches = re.findall(regex, text)
    for match in matches:
        props[match[0]] = match[1]
    return props

################################################################################
#
# _AndroidMessage
#
################################################################################
class _AndroidMessage(object):
    def __init__(this, code, component, command, args={}):
        this.code = code
        this.component = component
        this.command = command
        this.args = args

    def formatAsXml(this):
        """This method generates the xml ready to send over adb to the
        automation android service. Below is the formatting of the returned xml:

            <automation>
                <code>REQ</code>
                <component>vpn</component>
                <command>connect</command>
                <arglist>
                    <arg name="host">auto-asa.outside.com</arg>
                    ...
                </arglist>
            </automation>
        """

        root      = etree.Element('automation')
        code      = etree.SubElement(root, 'code')
        component = etree.SubElement(root, 'component')
        command   = etree.SubElement(root, 'command')
        arglist   = etree.SubElement(root, 'arglist')

        code.text      = this.code
        component.text = this.component
        command.text   = this.command

        for key in this.args.keys():
            arg = etree.SubElement(arglist, 'arg', {'name':key})
            arg.text = str(this.args[key])

        return etree.tostring(root) + '\n'

################################################################################
#
# __AndroidResponseMessage
#
################################################################################
class _AndroidResponseMessage(object):
    """This class represents the response received from the Android device."""

    def __init__(this, xml):
        this.xml = xml
        this.__parse()

    def __parse(this):
        root = etree.fromstring(this.xml)

        if root.tag.lower() != 'automation':
            raise AdbParseError('Not a valid automation message')

        this.code = root.findtext('code')
        this.component = root.findtext('component')
        this.command = root.findtext('command')
        this.error = root.findtext('error')
        this.data = root.findtext('data')

    def errorOccurred(this):
        return this.code != 'RES_ACK'

################################################################################
#
# AndroidCommunicator
#
################################################################################
class AndroidManager(object):
    __single = None
    adbRunning = False

    def __new__(classtype, *args, **kwargs):
        """Force this class to behave as a singleton."""
        if classtype != type(classtype.__single):
            classtype.__single = object.__new__(classtype, *args, **kwargs)
        return classtype.__single

    def __init__(this):
        this.vpn_host_port   = 7777
        this.vpn_device_port = 7778
        this.sys_host_port   = 7779
        this.sys_device_port = 7780
        this.mdm_host_port   = 7781
        this.mdm_device_port = 7782
        this.sys_sock        = None
        this.vpn_sock        = None
        this.mdm_sock        = None

    def connect(this, component, timeout=30):
        """Connects to the TCP socket setup by ADB forwarding for system
        communication"""
        if component == 'System':
            sock      = this.sys_sock
            host_port = this.sys_host_port
        elif component == 'Vpn':
            sock      = this.vpn_sock
            host_port = this.vpn_host_port
        elif component == 'Mdm':
            sock      = this.mdm_sock
            host_port = this.mdm_host_port
        else:
            raise NotImplementedError('component %s is ' % component +\
                'unsupported. connect() only supports components "System", '+\
                ' "Vpn", or "Mdm"')

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        start = time.time()
        while timeout > time.time()-start:
            try:
                sock.connect(('localhost', host_port))
                return sock
            except socket.error: pass
            time.sleep(1)
        raise AdbTimeout('Failed to connect to device socket for %s comm'
            % component)

    def disconnect(this, component):
        """Disconnects from the TCP socket setup by ADB forwarding for system
        communication"""
        if component == 'System':
            sock = this.sys_sock
        elif component == 'Vpn':
            sock = this.vpn_sock
        elif component == 'Mdm':
            sock = this.mdm_sock
        else:
            raise NotImplementedError('component %s is ' % component +\
                'unsupported. disconnect() only supports components "System" '+\
                'and "Vpn"')

        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
                sock.close()
            except socket.error, exc:
                LOGGER.error('SocketError encountered while disconnecting '+\
                    'from %s TCP socket: %s' % (component, repr(exc)));

    def __read_socket(this, bytes, sock):
        """Reads the number of bytes off the socket"""
        buf = ''
        while len(buf) < bytes:
            chunk = sock.recv(bytes-len(buf))
            if not len(chunk):
                raise AdbError('Socket connection reset')
            buf += chunk
        return buf.strip()

    def __setup_adb_forwarding(this):
        """Forwards all tcp traffic from a local port to a remote port on the
        Android device."""
        LOGGER.info('Setting up ADB forwarding')
        this.doAdbCmd('forward tcp:%s tcp:%s'
            % (this.sys_host_port, this.sys_device_port))
        this.doAdbCmd('forward tcp:%s tcp:%s'
            % (this.vpn_host_port, this.vpn_device_port))
        this.doAdbCmd('forward tcp:%s tcp:%s'
            % (this.mdm_host_port, this.mdm_device_port))

    def startAdbServer(this, timeout=30):
        """Starts the ADB server if not already running"""
        LOGGER.info('Starting ADB server')

        # Newer versions of ADB added public key authentication. When a device
        # is connected to an unauthorized host, ADB will generate a
        # public/private key pair to present to the device for acceptance.
        # These keys are stored beneath the directory specified by the HOME
        # environment variable. If this variable is not defined, ADB will not
        # start successfully. Since the automation service runs as root we
        # will specify /root if it is not set.
        home_var = 'HOME'
        if not os.environ.has_key(home_var):
            root_home = '/root'
            LOGGER.info('Setting HOME env variable to: "%s"' % root_home)
            os.environ[home_var] = root_home

        # To avoid recursive calls, we must declare ADB as running before
        # actually issuing the command. doAdbCmd() will again check to make
        # sure ADB is running before executing the command.
        this.adbRunning = True
        this.doAdbCmd('start-server')

        # Sometimes a device will fail to be recognized after starting ADB.
        # This can be resolved by rebinding the usb device.
        if not this.__is_device_available(timeout/2):
            this._rebind_device()

            if not this.__is_device_available(timeout/2):
                raise AdbError('Failed to find available device')

        this.__setup_adb_forwarding()

    def _rebind_device(this):
        """This method is a workaround to a bug with the android dev tools
        where a device will randomly report through ADB as 'offline'. By
        rebinding the usb driver on the specific port, the device will be
        reinitialized."""
        usb_dir = os.sep.join(['', 'sys', 'bus', 'usb', 'drivers', 'usb'])
        unbind_path = os.path.join(usb_dir, 'unbind')
        bind_path = os.path.join(usb_dir, 'bind')
        LOGGER.info('Rebinding usb device')
        # From experience it appears as though the device will always reside on
        # USB bus 1 port 1, aka '1-1'
        if subprocess.call("echo '1-1' > %s" % unbind_path, shell=True) != 0:
            LOGGER.error('Failed to unbind the usb device')
        time.sleep(2)
        if subprocess.call("echo '1-1' > %s" % bind_path, shell=True) != 0:
            LOGGER.error('Failed to bind the usb device')

    def __is_device_available(this, timeout):
        start = time.time()
        while time.time() - start < timeout:
            output = this.doAdbCmd('get-state')
            LOGGER.info('ADB state: %s' % output)
            if 'device' in output:
                return True
            time.sleep(1)
        return False

    def killAdbServer(this):
        """Stops the ADB server process"""
        LOGGER.info('Killing ADB server')
        this.disconnect('System')
        this.disconnect('Vpn')
        this.disconnect('Mdm')

        this.doAdbCmd('kill-server')
        this.adbRunning = False

    def sendSystemCommand(this, command, args={}, timeout=30):
        return this.__sendCommand('System', command, args, timeout)

    def sendVpnCommand(this, command, args={}, timeout=30):
        return this.__sendCommand('Vpn', command, args, timeout)

    def sendMdmCommand(this, command, args={}, timeout=30):
        return this.__sendCommand('Mdm', command, args, timeout)

    def __sendCommand(this, component, command, args={}, timeout=30):
        if not this.adbRunning:
            this.startAdbServer()

        # Connect to the socket and send the request
        code, msglen, data = this.__sendRequest('REQ', component, command, args,
            timeout)

        # validate request response code is REQ_ACK
        if code != 'REQ_ACK':
            raise AdbError('%s command request not acknowledged from device'
                % component)

        if component == 'System':
            # When making network modifications, TCP communication can become
            # unstable. To work around this we will wait briefly before
            # requesting the result of the command.
            if 'wifi' in command.lower():
                time.sleep(1)

        # Connect to the socket again to get the result of the request
        try:
            code, msglen, data = this.__sendRequest('RES', component, command, args,
                timeout)
        except AdbError, e:
            LOGGER.debug('Got AdbError: %s %s' % (repr(e), e.message))
            if e.message == 'Socket connection reset' and component == 'Mdm' and command == 'STOP_MDM_AUTO_SERVICE':
                return
            raise e.__class__(e.message)

        return _AndroidResponseMessage(data)

    def __sendRequest(this, code, component, command, args={}, timeout=30):
        try:
            sock = this.connect(component, timeout)

            # Send the request
            cmd = _AndroidMessage(code, component, command, args)
            xml = cmd.formatAsXml()

            LOGGER.info('Sending message: %s' % xml)
            sock.sendall(xml)

            # get return code and data to verify the device got the command
            code = this.__read_socket(7, sock)
            msglen = int(this.__read_socket(8, sock))
            data = this.__read_socket(msglen, sock)
            LOGGER.info('%s(%d): %s' % (code, msglen, data))

            this.disconnect(component)
        except socket.error, exc:
            raise AdbError('Android Automation %s Service error: %s'
                % (component, repr(exc)))
        except socket.timeout:
            raise AdbTimeout('Android Automation %s Service timeout'
                % component)

        return code, msglen, data

    def doAdbCmd(this, cmd, timeout=60):
        """This method is used to execute all ADB commands in a standard way.
        It forks off the command and polls it until a timeout expires to
        prevent subprocess from hanging a test."""

        # Make sure ADB has been started before issuing any commands.
        if not this.adbRunning:
            this.startAdbServer()

        cmd = 'adb %s' % cmd
        LOGGER.info("Executing ADB command: '%s'" % cmd)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT,
                      shell=True)
        start = time.time()
        while timeout > time.time() - start:
            if p.poll() is not None:
                if p.returncode != 0:
                    raise AdbError('ADB command (%s) returned code: %d'
                                   % (cmd, p.returncode))
                return p.communicate()[0]
            time.sleep(1)
        raise AdbTimeout("ADB command timed out(%ss): '%s'" % (timeout, cmd))

    def doAdbShellCmd(this, cmd, root=False, pipe_file=None, timeout=60):
        """This method constructs an 'adb shell' command. If root privileges
        are requested, 'su -c' will be used. Also, root level commands must
        be enclosed within parenthesis if the device does not have a root shell.
        An optional pipe file may be provided to dump the output into.
        """
        shell_cmd = 'shell'
        if root:
            shell_cmd += ' su -c'

            if HostInfo.HostInfo().isShellRoot():
                shell_cmd = '%s %s' % (shell_cmd, cmd)
            else:
                shell_cmd = '%s "%s"' % (shell_cmd, cmd)
        else:
            shell_cmd = '%s "%s"' % (shell_cmd, cmd)

        if pipe_file is not None:
            shell_cmd += ' > "%s"' % pipe_file

        return this.doAdbCmd(shell_cmd, timeout)

    def logInfo(this, message):
        """Logs an info message to Android Logcat."""
        this.doAdbShellCmd('log -p i -t AutomationHost "%s"' % message)

    def runApp(this, package, activity):
        LOGGER.info('Running app: %s/.%s' % (package, activity))
        output = this.doAdbShellCmd('am start ' + \
                                    '-a android.intent.action.MAIN ' + \
                                    '-n %s/%s' % (package, activity))
        if 'error' in output.lower():
            raise AdbError('Failed to start: %s/%s (%s)' % (package, activity,
                            output))

    def killApp(this, package, user_id=None):
        """Kill all processes associated with <package> (app's package name).
        This command kills only processes that are safe to kill and that will
        not impact the user experience.

        Optional <user_id> is to specify the user whose processes to kill; all
        users if not specified"""
        LOGGER.info('Killing app: %s' % package)
        cmd = 'am kill %s' % package
        if user_id is not None:
            cmd = cmd + ' --user %s' % user_id
        output = this.doAdbShellCmd(cmd)
        if 'error' in output.lower():
            raise AdbError('Failed to kill: %s (%s)' % (package, output))

    def forceStopApp(this, package):
        """Force stop everything associated with <package> (app's package
            name)."""
        LOGGER.info('Force-stop app: %s' % package)
        output = this.doAdbShellCmd('am force-stop %s' % package)
        if 'error' in output.lower():
            raise AdbError('Failed to force-stop: %s (%s)' % (package, output))

    def getProperties(this):
        output = this.doAdbShellCmd('getprop')
        return parseProperties(output)

    def getPropInfo(this, file):
        this.doAdbShellCmd('getprop', pipe_file=file)

    def getProcessInfo(this, file):
        this.doAdbShellCmd('ps -x', pipe_file=file)

    def getActivityInfo(this, file):
        this.doAdbShellCmd('dumpsys activity', pipe_file=file)

    def getMemInfo(this, file):
        this.doAdbShellCmd('dumpsys meminfo -a', pipe_file=file)

    def getConnectivityInfo(this, file):
        this.doAdbShellCmd('dumpsys connectivity', pipe_file=file)

    def getPackageInfo(this, file):
        this.doAdbShellCmd('pm list packages', pipe_file=file)

    def getTextFileData(this, remote_file, dest_file):
        this.doAdbShellCmd('cat %s' % remote_file, root=True, pipe_file=dest_file)

    def fileExists(this, directory, filename, root=False):
        """Returns if a filename exists in a given directory. If root is set,
        the command will be executed with root priviledges."""
        return filename in this.doAdbShellCmd('ls %s' % directory, root=root)

    def deleteFile(this, filename, root=False):
        """Deletes a file from the device. Raises an AdbError upon failure."""
        if 'failed' in this.doAdbShellCmd('rm %s' % filename, root=root):
            raise AdbError('Failed to delete: %s' % filename)

    def pushFile(this, local_file, remote_file=None):
        """Pushes a file onto the remote device. If remote_file is not
        specified, the file is placed in /sdcard/. Raises an AdbError upon
        failure."""
        if not os.path.exists(local_file):
            raise AdbError('File does not exist: %s' % local_file)

        if remote_file is None:
            # The /sdcard mount should always be writable by the shell
            remote_file = '/sdcard/%s' % os.path.basename(local_file)
            output = this.doAdbCmd('push %s %s' % (local_file, remote_file))
            this._verifyFileTransfer(output)
        elif HostInfo.HostInfo().isRooted() and not HostInfo.HostInfo().isShellRoot():
            # If the device is rooted but the shell runs in non-priviledged mode
            # we will need to push to a temporary location and then move it.
            # /data/local should do nicely. /sdcard is not a good choice
            # because we may get "cross-device link" errors.
            temp_dest = '/data/local/%s' % os.path.basename(local_file)
            output = this.doAdbCmd('push %s %s' % (local_file, temp_dest))
            this._verifyFileTransfer(output)
            this.doAdbShellCmd('mv %s %s' % (temp_dest, remote_file), root=True)
        else:
            # Just push directly to the remote file
            output = this.doAdbCmd('push %s %s' % (local_file, remote_file))
            this._verifyFileTransfer(output)

        return remote_file

    def pullFile(this, remote_file, local_file):
        """Pulls a file from the remote device. Raises an AdbError upon
        failure."""
        output = this.doAdbCmd('pull %s %s' % (remote_file, local_file))
        this._verifyFileTransfer(output)
        return local_file

    def _verifyFileTransfer(this, output):
        match = re.search('\(.* bytes in .*s\)', output)
        if match is None:
            raise AdbError("File transfer failed: %s" % output)

    def retrieveUid(this):
        """Requests a dictionary representing the UID for this android device,
        and returns the received UID."""
        rsp = this.sendSystemCommand('GET_UID')
        if rsp.errorOccurred():
            raise AdbError('Failed to get UID (%s)' % rsp.error)
        data = json.loads(rsp.data)
        return data['uid']
    
    def hasConnectivity(this, server, port):
        rsp = this.sendSystemCommand('HAS_CONNECTIVITY',
                                        {'server':server, 'port':port})
        if rsp.errorOccurred():
            raise SystemCommandException("HAS_CONNECTIVITY %s:%s failed: %s" % (server, port, rsp.error))
        
        return int(rsp.data) == 1
    
    def runCmd(this, cmd, expectedRet=None):
        d = {'cmd':cmd}
        if expectedRet:
            d['expectedRet'] = expectedRet
        
        rsp = this.sendSystemCommand('RUN_CMD', d)
        if rsp.errorOccurred():
            raise SystemCommandException("RUN_CMD %s failed: %s" % (cmd, rsp.error))
        
        return rsp.data
    
    def mount(this, directory):
        if HostInfo.HostInfo().isRooted():
            this.doAdbShellCmd('mount -o remount,rw %s' % directory, True)
