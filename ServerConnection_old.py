'''
Created on Mar 31, 2010

@author: Nate Roy (natroy@cisco.com)
@note: This file contains the code which performs the communication with
the Dashboard service on the automation server.
'''
import os
import socket
import struct
import time
import xml.etree.ElementTree as ET

from AutomationService.ServiceRunnerResponseMsg import ResponseMsg
import HostInfo
import sys
sys.path.append("E:\\automation")
import config
import subprocess
import logging
LOGGER = logging.getLogger("automation")

HOST_INFO = HostInfo.HostInfo()

class ResponseError(Exception): pass

class ServerConnection():
    '''
    This class encompasses the logic required to communicate with
    the dashboard service on the automation server
    '''

    def __init__(this):
        this.PORT        = 9876
        this.host        = '10.76.157.238'

    def __get(this,sock,length):
        """Gets length number of bytes off the socket"""
        buf = sock.recv(length)
        while len(buf) < length:
            buf += sock.recv(length-len(buf))
        return buf

    def __request(this,command):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # We don't really want a timeout. However, if something goes
        # wrong the socket shouldn't block forever. The next best
        # option is to set the timeout to a high value.
        sock.settimeout(300)

        # create a basic message object, that can be used in case something
        # goes wrong on the socket. If the socket transaction is successful a
        # new message object will be created with the "real" data
        msg = ResponseMsg('ecn',command)

        try:
            ip = socket.gethostbyname(this.host)
            #sock.connect((ip,this.PORT))
            this.__do_connect(sock, ip)
            sock.send(command)
            sock.shutdown(1) # tell the server we are done sending
            code   = this.__get(sock,3)
            msglen = struct.unpack("!i", this.__get(sock,4))[0]
            data   = this.__get(sock,msglen)
            # create the real response message object.
            msg = ResponseMsg(code,command,data)
            print print 
            print print

        # Windows raises socket.gaierror exceptiosn
        except socket.error, exc:
            LOGGER.exception(exc)
            msg.code = 'ecn'
            if exc.args[0] in [111, 113, 10061]:
                msg.error = \
                    "Could not establish connection to host '%s' (%s): %s" \
                    %(this.host,ip,exc.args[1])
            elif exc.args[0] in [0, 8, 11001, 11004]:
                msg.error = \
                    "Could not resolve hostname '%s' to a valid address: %s" \
                    %(this.host,exc.args[1])
            else:
                msg.error = str(exc)

        # mac/linux raises socket.error exceptiosn
        except socket.gaierror, exc:
            LOGGER.exception(exc)
            msg.code = 'ecn'
            if exc.args[0] in [8]:
                msg.error = \
                    "Could not resolve hostname '%s' to a valid address: %s" \
                    %(this.host,exc.args[1])
            else:
                msg.error = str(exc)

        except Exception, exc:
            LOGGER.exception(exc)
            msg.code = 'ecn'
            msg.error = str(exc)
        finally:
            sock.close()
            return msg

    def __do_connect(this, s, ip, attempts=3, backoff=1):
        '''
        Connects to the given ip to this.port for a certain
        amount of attempts. The backoff value is used to wait
        between attempts.
        '''
        for x in range(attempts):
            try:
                s.connect((ip,this.PORT))
            except socket.error, exc:
                # OS X sometimes gets blocked due to [Errno 48] Address already
                # in use, so try using the SO_REUSEADDR flag to tell the kernel
                # to reuse the socket in TIME_WAIT state without waiting for is
                # natural timeout to expire
                if HOST_INFO.isOSX() and exc.args[0] in [48]:
                    if this._can_debug_mac_socket_error():
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                LOGGER.debug("Cannot open socket: %s" % repr(exc))
                time.sleep(backoff)
                pass
            else:
                return
        raise

    def _can_debug_mac_socket_error(this, port=None):
        """OS X sometimes gets blocked due to [Errno 48] Address already in use.
        If this happens, check to see what processes are using the given port.
          - If only Python is using this port, return True and we can try using
            some method (like the SO_REUSEADDR flag) to fix the issue.
          - If another process is using this port, we dont want to use the
            above fix b/c it could interfere with that process. Instead, just
            log some of the details about that process and return False."""
        if port is None:
            port = this.PORT
        for proc in this._get_mac_processes_using_port(port):
            if proc.split()[0] != "Python":
                LOGGER.debug("A process besides Python is using port %s: %s"
                        % (port, proc))
                return False
        return True

    def _get_mac_processes_using_port(this, port=None, verbose=True):
        """Return a list of the processes that are using the specified port. If
        no port is specified, use this.PORT. If verbose is true, return all the
        details gathered by the lsof command. Otherwise, only return the name
        of each process."""
        if port is None:
            port = this.PORT
        p = subprocess.Popen("lsof -i -P | grep ':%s '" % port, shell=True,
                stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        (out, err) = p.communicate()
        if p.returncode != 0:
            LOGGER.debug("Unable to get processes using port %s> %s : %s"
                    % (port, out, err))
            return [-1]
        if verbose:
            return out.splitlines()
        else:
            return [line.split()[0] for line in out.splitlines()]

    def __to_argument_string(this, varname, varvalue):
        '''
        returns a string in the form:
        ' "varname=varvalue" ' to be passed to the dashboard service commands
        '''

        return '"' + str(varname) + '=' + str(varvalue) + '"'

    def setServerAddress(this, value):
        this.host = value

    def get_hostscan_version_request(this, asa_obj):
        '''
        Communicates with deathstar to retrieve the version of the
        hostscan/csd package loaded on given ASA.
        '''

        request = 'get_hostscan_version %s' % asa_obj.name
        response = this.__request(request)

        if response.code != 'ack':
            raise ResponseError('%s: %s' % (response.code, response.error))
        try:
            root = ET.fromstring(response.data)
        except Exception, exc:
            raise ResponseError('Error parsing response data: %s' %
                                repr(exc))
        if root.tag != 'version':
            raise ResponseError('Unexpected root tag, %s' % root.tag)

        return root.text, root.get('type')

    def get_asa_version_request(this, asa_obj):
        '''
        Communicates with deathstar to retrieve the version of the
        ASA package loaded on the given ASA.
        '''

        request = 'get_asa_version %s' % asa_obj.name
        response = this.__request(request)

        if response.code != 'ack':
            raise ResponseError('%s: %s' % (response.code, response.error))
        try:
            root = ET.fromstring(response.data)
        except Exception, exc:
            raise ResponseError('Error parsing response data: %s' %
                                repr(exc))
        if root.tag != 'version':
            raise ResponseError('Unexpected root tag, %s' % root.tag)

        return root.text

    def get_hostscan_asa_request(this):
        '''
        Communicates with deathstar to retrieve the name of the
        ASA associated with current host.
        '''
        request = 'get_hostscan_asa %s' % HOST_INFO.nodename
        response = this.__request(request)

        if response.code != 'ack':
            raise ResponseError('%s: %s' % (response.code, response.error))
        try:
            root = ET.fromstring(response.data)
        except Exception, exc:
            raise ResponseError('Error parsing response data: %s' %
                                repr(exc))
        if root.tag != 'asaName':
            raise ResponseError('Unexpected root tag, %s' % root.tag)
        asa_name = root.text
        if asa_name.lower() == 'none':
            asa_name = None
        return asa_name

    def get_default_asa_request(this, asa_type):
        '''
        Communicates with deathstar to retrieve the name of the
        ASA associated with current host.
        '''
        hostname = HOST_INFO.nodename
        request = ('get_default_asa %s %s') % (hostname, asa_type)
        response = this.__request(request)

        # if response is not ack, return the response code and error
        if response.code != 'ack':
            return response

        # else, do some processing to get the actual asa name before returning
        try:
            root = ET.fromstring(response.data)
        except Exception, exc:
            raise ResponseError('Error parsing response data: %s' % repr(exc))
        if root.tag != 'asaName':
            raise ResponseError('Unexpected root tag, %s' % root.tag)
        asa_name = root.text
        if asa_name.lower() == 'none':
            asa_name = None
        response.data = asa_name
        return response

    def get_testbed_resources(this, resource_type, capability):
        '''
        Makes a request to the server to find additional resources of a certain
        type and capability. Only the resources in the same testbed as this
        host will be considered. Returns a list of matching resource names.
            resource_type - The resource type. "asa", "ise", etc.
            capability    - The resource's capability. "vpn", "nam", etc.
        '''
        response = this.__request(('get_testbed_resources %s %s %s' %
                            (HOST_INFO.nodename, resource_type, capability)))
        if response.code != 'ack':
            return response

        # Verify the integrity of the response first.
        try:
            root = ET.fromstring(response.data)
        except Exception, exc:
            raise ResponseError('Error parsing response data: %s' % repr(exc))
        if root.tag != 'resources':
            raise ResponseError('Unexpected root tag, %s' % root.tag)

        # Return a list of resource names
        return [x.text for x in root.findall('resource')]

    def add_host_request(this):
        '''
        This method serves an an interface through which an automation machine can
        request to be added to the automation server's list of monitored machines.
        '''
        ip = this.getIp()
        request = ('add_host %s %s %s %s') %\
                    (this.__to_argument_string('host', HOST_INFO.nodename),this.__to_argument_string('os', HOST_INFO.os),this.__to_argument_string('arch', HOST_INFO.arch),this.__to_argument_string('ip', ip))
        LOGGER.debug("add host request %s" %request)
        return this.__request(request)
    def map_host_capability_request(this,module):
        request = ('host_capability_association %s %s') %\
                    (this.__to_argument_string('capability',module),this.__to_argument_string('host',HOST_INFO.nodename))
        LOGGER.debug("capability match request %s" %request)
        return this.__request(request)
    def add_host_resourcepool_request(this):
        request = ('add_host_resourcepool %s ') %\
                    (this.__to_argument_string('host',HOST_INFO.nodename))
        LOGGER.debug("adding to resourcepool request %s" %request)
        return this.__request(request)
    def getIp(this):
        if HOST_INFO.isWindows():

            p = subprocess.Popen('ipconfig',stdout=subprocess.PIPE)
            ip_details = p.stdout.readlines()
            for line in ip_details:
                if 'management' in line:
                    index_line = ip_details.index(line)
                    print(index_line)
                    print(ip_details[index_line + 4])
                    if HOST_INFO.isWindows7():
                        if ((ip_details[index_line + 3]).strip()).startswith('IPv4'):

                            machine_ip_address = ((ip_details[index_line + 3]).split(':'))[1].strip()
                            LOGGER.debug(machine_ip_address)
                            break
                    else:
                        if ((ip_details[index_line + 4]).strip()).startswith('IPv4'):

                            machine_ip_address = ((ip_details[index_line + 4]).split(':'))[1].strip()
                            LOGGER.debug(machine_ip_address)
                            break


        if HOST_INFO.isMac():
            p = subprocess.Popen(['ifconfig','-a'],stdout=subprocess.PIPE)
            output = p.communicate()[0]
            output = output.split(os.linesep)
            for line in output:
                if line.strip().startswith('inet '):
                    fields = line.strip().split()
                    machine_ip_address = fields[1]

        if HOST_INFO.isLinux():

            p = subprocess.Popen(['/sbin/ifconfig','-a'],stdout=subprocess.PIPE)
            output = p.communicate()[0]
            output = output.split(os.linesep)
            flag = False
            for line in output:
                if line.strip().startswith('management'):
                    flag = True
                elif (flag and line.strip().startswith('inet')):
                    fields = line.strip().split()
                    address = fields[1].strip().split(":")
                    machine_ip_address =  address[-1].strip()
                    break
        return machine_ip_address

    def remove_host_request(this):
        '''
        This method serves an an interface through which an automation machine can
        request to be removed from the automation server's list of monitored machines.
        '''
        request = ('remove_host %s') %\
                  (this.__to_argument_string('host', HOST_INFO.nodename))

        return this.__request(request)

    def vm_revert_snapshot_request(this, snapshot_name, snapshot_id):
        '''
        This method serves as an interface through which you can make
        a call to revert a snapshot on a VM
        '''
        # build the request string
        request = ('vm revert_to_snapshot %s %s %s') %\
                  (this.__to_argument_string('host', HOST_INFO.nodename),\
                   this.__to_argument_string('snapshot_name', snapshot_name),\
                   this.__to_argument_string('snapshot_id', snapshot_id))


        return this.__request(request)

    def vm_snapshot_exists_request(this, snapshot_name):
        '''
        This method serves as an interface through which you can make
        a call to the method to determine if a snapshot exists on a VM
        '''
        # build the request string
        request = ('vm snapshot_exists %s %s') %\
                  (this.__to_argument_string('host', HOST_INFO.nodename),\
                   this.__to_argument_string('snapshot_name', snapshot_name))

        return this.__request(request)

    def vm_create_snapshot_request(this, snapshot_name, power_cycle_vm=True):
        '''
        This method serves as an interface through which you can make
        a call to the method to create a snapshot on this VM.  It defaults
        to shutting down the VM and taking the snapshot then turning the machine
        back on afterwards.  If you pass power_cycle_vm as False, it will take
        a snapshot of the machine while it is still powered on.
        '''
        # build the request string
        request = ('vm create_snapshot %s %s %s') %\
                  (this.__to_argument_string('host', HOST_INFO.nodename),\
                   this.__to_argument_string('snapshot_name', snapshot_name),\
                   this.__to_argument_string('power_cycle_vm', str(power_cycle_vm)))

        return this.__request(request)

    def vm_snapshot_list_request(this):
        '''
        This method serves as an interface through which you can make
        a call to the method to list the snapshots on this VM.
        '''
        # build the request string
        request = ('vm list_snapshots %s') %\
                   (this.__to_argument_string('host', HOST_INFO.nodename))

        return this.__request(request)

    def vm_snapshot_remove_request(this, snapshot_name, snapshot_id):
        '''
        This method serves as an interface through which you can make
        a call to the method to remove snapshots.
        '''
        request = ('vm remove_snapshot %s %s %s') %\
                  (this.__to_argument_string('host', HOST_INFO.nodename),\
                   this.__to_argument_string('snapshot_name', snapshot_name),\
                   this.__to_argument_string('snapshot_id', snapshot_id))


        return this.__request(request)

    def vm_snapshot_rename_request(this, snapshot_name, new_name, snapshot_id):
        '''
        This method serves as an interface through which you can make
        a call to the method to rename snapshots.
        if snapshot_id:
        '''
        # build the request string
        request = ('vm rename_snapshot %s %s %s %s') %\
                  (this.__to_argument_string('host', HOST_INFO.nodename),\
                   this.__to_argument_string('snapshot_name', snapshot_name),\
                   this.__to_argument_string('new_name', new_name),\
                   this.__to_argument_string('snapshot_id', snapshot_id))


        return this.__request(request)

    def save_state_exists_query(this, file_name):
        '''
        This method asks the dashboard service on the server if there is
        a save state file for this machine located in it's FTP repo
        '''
        # build the request string
        request = ('save_state_exists %s %s') %\
                  (this.__to_argument_string('host', HOST_INFO.nodename),\
                   this.__to_argument_string('file_name', file_name))

        return this.__request(request)

    def switch_config_query(this, args):
        '''
        This method sends queries to the server to perform switch config
        changes.
        Parameters:

        args - any arguments that the query needs

        '''
        # start the command with 'switch_config' to tell the server we want
        # to perform some switch configuration
        request = "switch_config "

        # make sure to add the host to the arguments if it's not there
        if 'host' not in args:
            args['host'] = HOST_INFO.nodename

        # append all of the arguments
        for key, value in args.items():
            request = ('%s %s') % (request,\
                                   this.__to_argument_string(key, value))

        # do the request
        return this.__request(request)

    def netem_config_request(this, args):
        '''
        This method sends requests to the server to perform netem config
        changes.
        Parameters:
        args - any arguments the netem config needs
        '''

        # start the command with 'netem_config' to tell the server we want
        # to perform some switch configuration
        request = "netem_config "

        # append args in form of '"arg1=value1"' to the request
        request += ''.join([this.__to_argument_string(key, value) + ' ' \
                   for key,value in args.iteritems()])
        # do the request
        return this.__request(request)

    def results_checksum(this,directory = None):
        request = "results_checksum %s" %(directory)
        return this.__request(request)

    def results_import(this,import_type,directory):
        request = "results_import %s %s" %(import_type, directory)
        return this.__request(request)


if __name__ == "__main__":

    # import sys so we can see logging when running standalone
    #import sys
    #LOGGER.setLevel(logging.DEBUG)
    #LOGGER.addHandler(logging.StreamHandler(sys.stdout))

    s = ServerConnection()
    s.add_host_request()
    #a = {'op' : 'swap_network', 'vlan' : '202'}
    #s.switch_config_query(a)
    #m = s.vm_snapshot_list_request()
    #print str(m.data)

    #nm_args = {'server':'10.86.101.139',
    #        'username': 'root',
    #        'password' : 'root:q',
    #        'client_ip' : '1.1.1.56',
    #        'client_speed' : '100Mbps',
    #        'client_delay' : 200,
    #        'client_pkt_loss' : 11.5}

    #m = s.netem_config_request(nm_args)

    #m = s.vm_revert_snapshot_request("clean-03-10")
    #print str(m.data)

    #m = s.vm_snapshot_exists_request("clean-base")
    #print str(m.data)
#
#    m = s.vm_create_snapshot_request("clean-base")
#    print str(m)
#
#    m = s.vm_snapshot_remove_request("clean-base")
#    print str(m)
#
#    m = s.vm_snapshot_rename_request("clean-base", "not-clean-base")
#    print str(m)
#
#    m = s.save_state_exists_query("save_state.pickle")
#    print str(m.data)
