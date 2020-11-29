"""
This script will install the python automation service into the system,
enable the service to start automatically, and start the service. On windows,
it will also install a tray icon.
"""
import sys
import os.path
import platform
import subprocess
import socket
import paramiko
import config

# Instantiating an object of _config class for registration/removal of machine entry from deathstar
W_config=config._config()
file_path = W_config.HOST_INFO_PATH


# Ensure we are using a new enough version of python
if not (sys.version_info[0] == 2 and sys.version_info[1] >= 5):
    print """
        ERROR: Automation requires cpython/jython 2.7 or newer!
        please upgrade to continue.
    """
    sys.exit(1)

# Figure out the base directory
BASEDIR = sys.path[-1] # Best guess
LIBDIR  = BASEDIR + os.sep + 'lib'

# Look for our real path
for path in sys.path:
    if os.path.exists(path + os.sep + 'setup.py'):
        BASEDIR = os.path.normpath(path)
        LIBDIR = os.path.join(BASEDIR,'lib')
        break
sys.path.append(LIBDIR)
os.chdir(LIBDIR)

import getpass
import HostInfo
import subprocess
import ServerConnection
SERVICE_NAME = 'CiscoAutomationRunner'

HOST_INFO = HostInfo.HostInfo()

# This is the init script that will be used on Linux systems
REDHAT_INIT_SCRIPT = """#!/bin/bash
#
# %(SERVICE_NAME)s
#
# Author:       Matt Herbert <matherbe@cisco.com>
#
# chkconfig:    345 50 50
# description: %(SERVICE_NAME)s provides a framework for executing \\
#              automated testing.
# processname: ServiceRunner.py
# pidfile: /var/run/%(SERVICE_NAME)s.pid
# config: /automation/config.py

### BEGIN INIT INFO
# Provides: %(SERVICE_NAME)s
# Required-Start: $network
# Required-Stop:
# Default-Start: 2 3 4 5
# Default-Stop: 0 1 6
# Short-Description: Cisco Automation Service
# Description:  %(SERVICE_NAME)s provides a framework for executing
#              automated testing.
### END INIT INFO

# source function library
. /etc/init.d/functions


MY_SERVICE_NAME="%(SERVICE_NAME)s"
PATH=$PATH:/usr/local/bin
export PATH
RETVAL=0

start() {
    status $MY_SERVICE_NAME
    RETVAL=$?
    if [ $RETVAL -ne 0 ]; then
        echo -n $"Starting Cisco Automation Service: "
        daemon "%(EXECUTABLE)s /automation/lib/ServiceRunner.py &"
        RETVAL=$?
        echo
        [ $RETVAL -eq 0 ] && touch /var/lock/subsys/$MY_SERVICE_NAME
    fi
}

stop() {
    echo -n $"Stopping Cisco Automation Service: "
    killproc $MY_SERVICE_NAME
    echo
    [ $RETVAL -eq 0 ] && rm -f /var/lock/subsys/$MY_SERVICE_NAME
}

restart() {
    stop
    start
}

case "$1" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    restart
    ;;
  condrestart|try-restart)
    [ -f /var/lock/subsys/$MY_SERVICE_NAME ] && restart
    ;;
  status)
    status $MY_SERVICE_NAME
    RETVAL=$?
    ;;
  *)
    echo $"Usage: $0 {start|stop|status|restart|condrestart}"
    exit 1
esac

exit $RETVAL
RETVAL=0
""" %dict(SERVICE_NAME=SERVICE_NAME,
          EXECUTABLE=sys.executable)

UBUNTU_INIT_SCRIPT = """#!/bin/bash
### BEGIN INIT INFO
# Provides:          %(SERVICE_NAME)s
# Required-Start:    $networking
# Required-Stop:
# Should-Start:      $network
# Should-Stop:       $network
# X-Start-Before:
# X-Stop-After:      network
# Default-Start:     2 3 4 5
# Default-Stop:      1
# Short-Description: Cisco Automation Service
### END INIT INFO

PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
DAEMON="%(EXECUTABLE)s /automation/lib/ServiceRunner.py"
NAME='%(SERVICE_NAME)s'
PIDFILE=/var/run/$NAME.pid
DESC="Cisco Automation Service"

unset TMPDIR

. /lib/lsb/init-functions

function start() {
    if [ -s $PIDFILE ] && kill -0 $(cat $PIDFILE) >/dev/null 2>&1; then
        log_progress_msg "apparently already running"
        log_end_msg 0
        exit 0
    fi
    return $(start-stop-daemon --start --quiet --oknodo --background \\
                               --make-pidfile --pidfile "$PIDFILE" \\
                               --exec $DAEMON && success=1)
}

function stop() {
    # Jython will fork a child process with a different pid. We must try to
    # kill all children gracefully first. This should have no effect if running
    # with cpython.
    local ppid=$(cat "$PIDFILE")
    for child_pid in $(ps -o pid --no-headers --ppid ${ppid}); do
        kill -HUP $child_pid
    done
    return $(start-stop-daemon --stop --quiet --retry 5 --oknodo \
                               --pidfile $PIDFILE && success=1)
}

case "$1" in
  start)
        log_begin_msg "Starting $DESC: $NAME"
        start
        log_end_msg $?
        ;;
  stop)
        log_begin_msg "Stopping $DESC: $NAME"
        stop
        log_end_msg $?
        ;;
  restart)
        log_begin_msg "Restarting $DESC: $NAME"
        if stop; then
            start
        fi
        log_end_msg $?
        ;;
  status)
        status_of_proc -p $PIDFILE $NAME "$DESC" && exit 0 || exit $?
        ;;
  *)
        N=/etc/init.d/${0##*/}
        echo "Usage: $N {start|stop|restart|status}" >&2
        exit 1
        ;;
esac

exit 0
""" % dict(SERVICE_NAME=SERVICE_NAME,
           EXECUTABLE=sys.executable)


"""Root folder is not permissible to write
in Mac 11.Hence from Mac 11,automation folder 
will be under /Users/autobot"""
if (HOST_INFO.isMac() and HOST_INFO._detect_version()>'10.15'):
     MAC_PLIST_FILE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>%(SERVICE_NAME)s</string>
	<key>Program</key>
	<string>%(EXECUTABLE)s</string>
	<key>ProgramArguments</key>
	<array>
		<string>%(EXECUTABLE)s</string>
		<string>/Users/autobot/automation/lib/ServiceRunner.py</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
</dict>
</plist>
""" % dict(SERVICE_NAME=SERVICE_NAME,
           EXECUTABLE=sys.executable)
else:
     MAC_PLIST_FILE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>Label</key>
        <string>%(SERVICE_NAME)s</string>
        <key>Program</key>
        <string>%(EXECUTABLE)s</string>
        <key>ProgramArguments</key>
        <array>
                <string>%(EXECUTABLE)s</string>
                <string>/automation/lib/ServiceRunner.py</string>
        </array>
        <key>RunAtLoad</key>
        <true/>
        <key>KeepAlive</key>
        <true/>
</dict>
</plist>
""" % dict(SERVICE_NAME=SERVICE_NAME,
           EXECUTABLE=sys.executable)
     


def run_cmd(cmd):
    """Fetches the ip address of the machine
       Calls function to establish connection to deathstar
       Parameter : cmd for mac and linux: ifconfig
                   cmd for widnows: ipconfig
       returns host_ip,sftp,lines """
    prefix = W_config.NETWORKS.Management.Prefix
    s = subprocess.Popen(cmd, shell = 'True', stdout = subprocess.PIPE)
    output = s.communicate()[0]
    data = output.split()
    for i in data:
        if i.startswith(prefix):
            index_i = data.index(i)
            break
    host_ip=data[index_i]
    sftp,lines = ssh_to_deathstar()
    return host_ip,sftp,lines

def check(lines,host_ip,host_name=''):
    """Checks if the host ip address or the host name is already registered in deathstar
       parameters : lines from /etc/hosts file, host ip, host name
       return True if a match is found, else returns false """
    flag = False
    for line in lines:
        ip_address = line.split()[0]
        machine_name = line.split()[1]
        if host_ip == ip_address: 
            print("IP address {} already registered in deathstar".format(host_ip))
            flag = True
            break
        elif host_name == machine_name:
            print("Host name {} already registererd in deathstar".format(host_name))
            flag = True
            break
    return flag

def ssh_to_deathstar():
    #Establishes SSH Connection to deathstar, opens /etc/hosts and stores the lines of the file
    ip = W_config.DEATHSTAR_IP
    username = W_config.DEATHSTAR_USERNAME
    password = W_config.DEATHSTAR_PASSWORD
    try:
        print ("Connecting to deathstar..............")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip,22,username,password)
        sftp=ssh.open_sftp()
        f = sftp.open(file_path,'r')
        lines = f.readlines()
        print ("Successfully connected to deathstar")
    except:
        e = sys.exc_info()[0]
        print e
        print ("Failed to establish SSH connection")
    return sftp,lines

def register_machine(host_ip,sftp,lines):
    """ Registers the machine in deathstar
        Parameters : host ip, sftp,lines of the /etc/hosts file
        Checks if the machine is already registered, if it is not registered, writes the machine info to /etc/hosts file"""
    host_name = socket.gethostname()
    machine_info = host_ip+"   "+host_name+"\n"
    print ("Machine's ip address and hostname is {}".format(machine_info))
    if (check(lines,host_ip,host_name)):
        print ("Cannot register the machine in deathstar")
    else:
        try:
            f = sftp.open(file_path,'a')
            f.write(machine_info)
            f.close()
            print ("Machine registered successfully")
        except:
            print ("Unable to open \etc\host file")

def remove_machine_registration(host_ip,sftp,lines):
    """ Removes a machine entry from deathstar's /etc/hosts file
        Parameters : host ip, lines
        Checks if the machine is registered, if it is, it is removed from /etc/hosts"""
    if (check(lines,host_ip)):
        try:
            new_file = sftp.open(file_path,'w')
            for line in lines:
                if line.split()[0]!= host_ip:
                    new_file.write(line)
            new_file.close()
            print ("Removed the machine entry from deathstar successfully")
        except:
            print("Unable to open \etc\host file")
    else:
        print("Machine is not registered under deathstar")


################################################################################
#
# Windows Installation
#
################################################################################
if HOST_INFO.isWindows():

    import win32service
    import win32serviceutil
    import pywintypes
    import _winreg
    import win32api, win32pdhutil, win32con

    def __install_automation_win32():
        # Try to stop and remove any old service first
        try: win32serviceutil.StopService(SERVICE_NAME)
        except pywintypes.error, err: pass
        try: win32serviceutil.RemoveService(SERVICE_NAME)
        except pywintypes.error, err: pass

        service_rnr = os.sep.join([LIBDIR, 'ServiceRunner.py'])
        subprocess.check_call([
                sys.executable,
                service_rnr,
                '--interactive',
                '--startup',
                'auto',
                'install'])
        subprocess.check_call([sys.executable, service_rnr,'start'])
        print "success!"

        # Install the Tray Icon
        print "\n>> Installing Automation Tray Icon ... "
        tray_icon_path = '"'+os.path.join(BASEDIR, 'lib', 'TrayIcon.pyw') +'"'
        regpath = 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run'
        key = _winreg.OpenKeyEx(_winreg.HKEY_LOCAL_MACHINE, \
                    regpath, 0, _winreg.KEY_SET_VALUE)
        _winreg.SetValueEx(key, 'AutomationTrayIcon', 0, _winreg.REG_SZ, tray_icon_path)
        _winreg.CloseKey(key)
        print "Starting '%s'" %tray_icon_path
        os.startfile(tray_icon_path)
        print "success!"

        #Register machine in deathstar
        host_ip,sftp,lines = run_cmd('ipconfig')
        register_machine(host_ip,sftp,lines)
        
        

    def __remove_automation_win32():
        # remove any old service
        try: win32serviceutil.StopService(SERVICE_NAME)
        except pywintypes.error, err: pass
        try: win32serviceutil.RemoveService(SERVICE_NAME)
        except pywintypes.error, err: pass


        # Remove the Tray Icon
        print "\n>> Removing Automation Tray Icon ... "
        tray_icon_path = '"'+os.path.join(BASEDIR, 'lib', 'TrayIcon.pyw') +'"'
        try:

            regpath = 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run'
            key = _winreg.OpenKeyEx(_winreg.HKEY_LOCAL_MACHINE, \
                    regpath, 0, _winreg.KEY_SET_VALUE)
            _winreg.DeleteKey (key, 'AutomationTrayIcon')
            _winreg.CloseKey(key)
        except WindowsError:
            _winreg.CloseKey(key)

        print "Killing automation tray processes"
        try:
            #get process id's for the given process name
            pids = win32pdhutil.FindPerformanceAttributesByName('pythonw')

            for p in pids:
                print "Killing: %s" % (str(p))
                #get process handle
                handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, 0, p)
                win32api.TerminateProcess(handle,0) #kill by handle
                win32api.CloseHandle(handle) #close api
            print "success!"
            #Remove the machine entry from deathstar
            host_ip,sftp,lines = run_cmd('ipconfig')
            remove_machine_registration(host_ip,sftp,lines)
        except:
            print "failed to remove tray icon"


################################################################################
#
# Linux Installation
#
################################################################################
elif HOST_INFO.isLinux():
    def __install_automation_linux():
        # ensure permissions
        check_user()

        if 'ubuntu' in HOST_INFO.os.lower() or \
           'debian' in HOST_INFO.os.lower():
            # put init script in place
            fp = open('/etc/init.d/%s'%SERVICE_NAME, 'w')
            fp.write(UBUNTU_INIT_SCRIPT)
            fp.close()
            os.chmod('/etc/init.d/%s'%SERVICE_NAME, 0755)

            # update rc scripts
            rc = subprocess.call('update-rc.d %s defaults' %(SERVICE_NAME),\
                    shell=True)
            if rc != 0:
                print "Error registering automation service (rc=%s)!" %rc
                exit(rc)

        elif 'redhat'  in HOST_INFO.os.lower() or \
             'red hat' in HOST_INFO.os.lower() or \
             'fedora'  in HOST_INFO.os.lower() or \
             'centos'  in HOST_INFO.os.lower():
            # put init script in place
            fp = open('/etc/init.d/%s'%SERVICE_NAME, 'w')
            fp.write(REDHAT_INIT_SCRIPT)
            fp.close()
            os.chmod('/etc/init.d/%s'%SERVICE_NAME, 0755)

            # add to chkconfig
            rc = subprocess.call('chkconfig --add ' + SERVICE_NAME, shell=True)
            if rc != 0:
                print "Error registering automation service (rc=%s)!" %rc
                exit(rc)

        # start service
        rc = subprocess.call('service ' + SERVICE_NAME + ' start', shell=True)
        if rc != 0:
            print "Error starting automation service (rc=%s)!" %rc
            exit(rc)
        print "success!"

        #Register machine in deathstar
        host_ip,sftp,lines = run_cmd('ifconfig')
        register_machine(host_ip,sftp,lines)
        

    def __remove_automation_linux():
        # ensure permissions
        check_user()

        # stop service
        rc = subprocess.call('service ' + SERVICE_NAME + ' stop', shell=True)

        if 'ubuntu' in HOST_INFO.os.lower() or \
           'debian' in HOST_INFO.os.lower():
            # update rc scripts
            rc = subprocess.call('update-rc.d -f %s remove' %(SERVICE_NAME),\
                    shell=True)
            if rc != 0:
                print "Error un-registering automation service (rc=%s)!" %rc
                exit(rc)
        elif 'redhat'  in HOST_INFO.os.lower() or \
             'red hat' in HOST_INFO.os.lower() or \
             'fedora'  in HOST_INFO.os.lower() or \
             'centos'  in HOST_INFO.os.lower():
            # remove from chkconfig
            rc = subprocess.call('chkconfig --del ' + SERVICE_NAME, shell=True)
            if rc != 0:
                print "Error un-registering automation service (rc=%s)!" %rc
                exit(rc)

        # remove rc script
        os.unlink('/etc/init.d/%s' %SERVICE_NAME)
        print "success!"
        #Remove machine entry from deathstar
        host_ip,sftp,lines = run_cmd('ifconfig')
        remove_machine_registration(host_ip,sftp,lines)


################################################################################
#
# MAC OS-X Installation
#
################################################################################
elif HOST_INFO.isMac():
    def __install_automation_mac():
        # put init script in place
        plist_file = '/Library/LaunchDaemons/%s.plist'%SERVICE_NAME
        fp = open(plist_file, 'w')
        fp.write(MAC_PLIST_FILE)
        fp.close()

        # update launchd
        rc = subprocess.call('launchctl load -wF %s' %(plist_file),\
                shell=True)
        if rc != 0:
            print "Error registering automation service (rc=%s)!" %rc
            exit(rc)

        # start service
        rc = subprocess.call('launchctl start %s' %SERVICE_NAME, shell=True)
        if rc != 0:
            print "Error starting automation service (rc=%s)!" %rc
            exit(rc)
        print "success!"

        #Register machine in deathstar
        host_ip,sftp,lines = run_cmd('ifconfig')
        register_machine(host_ip,sftp,lines)
        

    def __remove_automation_mac():
        # stop service
        rc = subprocess.call('launchctl stop %s' %SERVICE_NAME, shell=True)

        # update launchd
        plist_file = '/Library/LaunchDaemons/%s.plist'%SERVICE_NAME
        rc = subprocess.call('launchctl unload -wF %s' %(plist_file),\
                shell=True)
        if rc != 0:
            print "Error un-registering automation service (rc=%s)!" %rc
            exit(rc)

        # remove plist file
        os.unlink(plist_file)
        print "success!"
        #Remove machine entry from deathstar
        host_ip,sftp,lines = run_cmd('ifconfig')
        remove_machine_registration(host_ip,sftp,lines)


################################################################################
#
# Platform Independent
#
################################################################################
def check_user():
    """Ensure that we are a who has permissions to perform the install"""
    # On RHEL 8.0, even though elevated privileges are given to autobot, getuser will return 'autobot' in place of 'root'
    # Check changes for rhel 8.0
    if not HOST_INFO.isRedhat8_0():
        if sys.platform.startswith('linux') and not getpass.getuser() == 'root':
            print "ERROR: You must be root to install automation!"
            exit(1)
        elif sys.platform.startswith('darwin') and not getpass.getuser() == 'root':
            print "ERROR: You must be root to install automation!"
            exit(1)
    else:
        if sys.platform.startswith('linux') and not getpass.getuser() == 'autobot':
            print "ERROR: You must be root to install automation!"
            exit(1)

def register_host(module,testbed_id):
    """request that this machine get added to the automation database"""
    print "\n>> Sending request to automation server to be added to database"
    sc = ServerConnection.ServerConnection()
    msg = sc.add_host_request()
    

    if msg.code == 'ack':
        print "success"
        msg = sc.map_host_capability_request(module)
        if msg.code == 'ack':
            print ("resource is mapped with capability %s" %module)
            if testbed_id:
                msg = sc.add_host_resourcepool_request()
                print msg.code
        else:
            print "WARNING: could not add this host to the database: " + \
                str(msg.error)
        
            
    else:
        print "WARNING: could not add this host to the database: " + \
                str(msg.error)

def unregister_host():
    """request that this machine get removed from the automation database"""
    print "\n>> Sending request to automation server to be removed from database"
    sc = ServerConnection.ServerConnection()
    msg = sc.remove_host_request()

    if msg.code == 'ack':
        print "success"
    else:
        print "WARNING: could not remove this host from the database: " + \
                str(msg.error)

def install_automation(module,resource_pool_flag=False):
    """Installs the automation and adds the host to the DB"""
    print ">> Installing Cisco Automation Service ... "
    if HOST_INFO.isWindows():
        __install_automation_win32()
    elif HOST_INFO.isLinux():
        __install_automation_linux()
    elif HOST_INFO.isMac():
        __install_automation_mac()
    else:
        raise NotImplementedError('platform %s not yet supported' %
                                  HOST_INFO.system)
    register_host(module,resource_pool_flag)
    # if resource_pool_flag:
        # register_host(module,'resource-pool')
    # else:
        # register_host()
    
    

def remove_automation():
    """removes the automation and remove the host from the DB"""
    print ">> Removing Cisco Automation Service ... "
    if HOST_INFO.isWindows():
        __remove_automation_win32()
    elif HOST_INFO.isLinux():
        __remove_automation_linux()
    elif HOST_INFO.isMac():
        __remove_automation_mac()
    else:
        raise NotImplementedError('platform %s not yet supported' %
                                  HOST_INFO.system)

    unregister_host()


def usage():
    print "setup.py [-h, -r]"
    print ""
    print "Automation setup script:"
    print "Run without arguments to install automation"
    print "Run with --remove or -r to remove automation and"\
            + " remove host from DB"
    exit()

def exit(rc=0):
    sys.stdout.write("\n\nPress return to exit...")
    sys.stdin.readline()
    sys.exit(rc)

if __name__ == "__main__":
    
    if len(sys.argv) > 1 and 'install' in sys.argv[1]:
        module = sys.argv[2]
        print module
        
        #print type(sys.argv[3])
        if 'resource-pool' in sys.argv[3]:
            resource_pool_flag = True
            install_automation(module,resource_pool_flag)
        else:
            install_automation(module)
        
    elif '-r' in sys.argv[1]:
        remove_automation()
    else:
        usage()

    exit()
