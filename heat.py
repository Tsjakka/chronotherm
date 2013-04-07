#!/usr/bin/env python
#------------------------------------------------------------------------------#
#
# Module:      Heat
#
# Project:     Chronotherm
# File:        heat.pl
# Created:     2013-02-01
# Author:      Tsjakka
# Copyright:   Open source
# Changes:     2013-04-07, Tsjakka: First release into github
#              
#------------------------------------------------------------------------------#

import os
import grp
import signal
import daemon
import lockfile
import argparse
import threading
import poplib, smtplib, email, mimetypes
import smbus
import time
from datetime import datetime
from time import sleep

POP_SERVER   = 'pop.googlemail.com'
POP_PORT     = '995'
EMAIL_USER   = '<enter email address here>'
EMAIL_PASSWD = '<enter email password here>'
SMTP_SERVER  = 'smtp.gmail.com'
EMAIL_DEST   = '<enter email address here>'

TIMEOUT       = 600
ADDRESS       = 0x20        # I2C address of MCP23008
NO_BUTTON     = 0x00
START_PROGRAM = 0x01
HOLD_TEMP     = 0x02
WARMER        = 0x04
COOLER        = 0x08
HOLD_TIME     = 0.3         # Time to hold a keypress
IDLE_TIME     = 0.6         # Time between kepresses
MIN_TEMP      = 7           # Minimum temperature allowed by the Chronotherm
MAX_TEMP      = 20          # Maximum temperature I allow to set remotely

class ChronothermController:
    '''Class for handling commands for the Chronotherm'''
    commands = []
    sendmail = False
    
    def init(self, sendmail_arg = False, command_arg = None):
        if command_arg is not None:
            self.commands.append(command_arg)

        self.sendmail = sendmail_arg
        
        # Set all of bank A to outputs on the I2C chip in the ChronoTherm
        bus = smbus.SMBus(0)
        bus.write_byte_data(ADDRESS, 0x00, 0x00)

        return

    def start_program(self, temp = None):
        '''Start the Chronotherm's regular program'''
        self.push_button(START_PROGRAM)
        if temp is not None:
            self.set_temp(temp)

        return
       
    def hold_temp(self, temp = None):
        '''Set the Chronotherm to a constant temperature. If temp is not specified, the current setting is used'''
        self.push_button(HOLD_TEMP)
        if temp is not None:
            self.set_temp(temp)

        return

    def set_temp(self, temp):
        '''Set a new temp. If the program is running, this will be a temporary setting'''
        # To make this independent of the current setting, first lower the temperature to 7C, the minimum.
        # Assume the current setting never exceeds MAX_TEMP + 2.
        for x in range(0, MAX_TEMP + 2 - MIN_TEMP):
            self.push_button(COOLER)

        # Let's limit the temperature to avoid accidents
        if temp > MAX_TEMP:
            temp = MAX_TEMP

        # Now move back up to the requested temp
        for x in range(temp - MIN_TEMP):
            self.push_button(WARMER)

        return
       
    def push_button(self, button):
        '''Open an IO port on the I2C chip and close it again, simulating a button press'''
        bus = smbus.SMBus(0)
        bus.write_byte_data(ADDRESS, 0x09, button)
        #print "Pressed: " + str(button)
        sleep(HOLD_TIME)
        bus.write_byte_data(ADDRESS, 0x09, NO_BUTTON)
        sleep(IDLE_TIME)
        
    def check_email(self):
        subject = None
        M = poplib.POP3_SSL(POP_SERVER, POP_PORT)
        M.user(EMAIL_USER)
        M.pass_(EMAIL_PASSWD)
        numMessages = len(M.list()[1])

        for i in range(numMessages):
            mail = email.message_from_string('\n'.join(M.retr(i + 1)[1]))

            # Handle SMS attachments and body
            attachment = None
            contents = None
            for part in mail.walk():
                # multipart/* are just containers
                #print part.get_content_type()
                if part.get_content_maintype() == 'multipart':
                    continue
                if part.get_content_type() == 'text/plain':
                    # Handle body
                    contents = str(part.get_payload())
                    continue
                filename = part.get_filename()
                if filename == 'text_0.txt':
                    attachment = part.get_payload(decode = True)

            # Handle subject
            subject = mail["Subject"]

            # Look in attachment, body and subject for commands
            if subject is not None:
                print "Subject: " + subject
                self.commands.append(subject)
            if contents is not None:
                print "Contents: " + contents
                for line in contents.split('\n'):
                    self.commands.append(line)
            if attachment is not None:
                print "Attachment: " + attachment
                self.commands.append(attachment)
            
            # Remove the email
            M.dele(i + 1)
           
        M.quit()
   
    # Accept the following commands
    # HEAT ON [<temp>] [[<dd-mm>] <hour:min>]  - Start program (with optional temporary temperature and/or date and/or time)
    # HEAT OFF [<temp>] [[<dd-mm>] <hour:min>] - Keep constant temperature (with optional temperature and/or date and/or time)
    # HEAT <temp>                              - Set a new temperature (constant or temporary, depending on current mode)
    def handle_commands(self):
        '''Handle commands from the received emails or the commandline'''
        par1 = None
        par2 = None
        par3 = None
            
        for command in self.commands:
            parts = command.split()
            if len(parts) == 0:
                self.commands.remove(command)
                continue
                
            # First check for a date and / or time in the last two parameters
            if ':' in parts[len(parts) - 1]:
                now = datetime.now()
                start = datetime.now()
                try:
                    start_time = datetime.strptime(parts[len(parts) - 1], '%H:%M')
                    
                    if '-' in parts[len(parts) - 2]:
                        start_date = datetime.strptime(parts[len(parts) - 2], '%d-%m')
                        start = start_time.replace(now.year, start_date.month, start_date.day)
                    else:
                        start = start_time.replace(now.year, now.month, now.day)

                    # Check if the time has come
                    if start > now:
                        continue
                except ValueError:
                    self.commands.remove(command)
                    print 'Error in date or time'
                    continue

            print 'Handling command: ' + command
            par1 = parts[0].lower()
            if (par1 == 'heat'):
                if (len(parts) > 1):
                    par2 = parts[1].lower()
                    if (par2.isdigit()):
                        self.set_temp(int(par2))
                        subject = "Temperature set to " + par2 + " degrees temporarily"
                    elif (par2 == 'on'):
                        if (len(parts) > 2):
                            par3 = parts[2].lower()
                        if par3 is not None and par3.isdigit():
                            self.start_program(int(par3))
                            subject = "Program started, temperature temporarily set to " + par3 + " degrees"
                        else:
                            self.start_program()
                            subject = "Program started"
                    elif (par2 == 'off'):
                        if (len(parts) > 2):
                            par3 = parts[2].lower()
                        if par3 is not None and par3.isdigit():
                            self.hold_temp(int(par3))
                            subject = "Hold Temp pressed, temperature set to a constant " + par3 + " degrees"
                        else:
                            self.hold_temp()
                            subject = "Hold Temp pressed, current temperature setting used"
                    else:
                        subject = "Unknown parameter parsed from email: " + par2
                else:
                    subject = "Command missing"
            else:
                subject = "Unknown command parsed from email: " + par1
                
            # Remove the command
            self.commands.remove(command)

            # Log the action
            print subject
            
            # If wanted, send an email
            if (self.sendmail):
                self.send_email(EMAIL_USER, EMAIL_PASSWD, EMAIL_USER, EMAIL_DEST, subject, 'Sent by heat.py')

    def send_email(self, usr, psw, fromaddr, toaddr, subject, msg):
        # Initialize SMTP server
        server = smtplib.SMTP_SSL(SMTP_SERVER)
        #server.starttls()
        server.login(usr, psw)
       
        # Send email
        senddate = datetime.strftime(datetime.now(), '%Y-%m-%d')
        m = "Date: %s\r\nFrom: %s\r\nTo: %s\r\nSubject: %s\r\nX-Mailer: My-Mail\r\n\r\n" % (senddate, fromaddr, toaddr, subject)
           
        server.sendmail(fromaddr, toaddr, m + msg)
        server.quit()


def daemon_loop(timeout, sendmail):
    '''Endless loop for when running as a daemon'''
    cc = ChronothermController()
    cc.init(sendmail)

    # Loop forever
    while True:
        cc.check_email()
        cc.handle_commands()
        sleep(timeout)
        
    return

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Daemon for handling control commands received by email.')
    parser.add_argument('command', type=str, nargs='*',
                        help='Command (just for debugging; program exits after processing)\n' +
                             'Command syntax: HEAT (ON|OFF) [<temp>] [[<dd-mm>] <hour:min>]')
    parser.add_argument('-t', dest='timeout', type=int, default=TIMEOUT,
                        help='Timeout in seconds between checking for email (default=%(default)s)')
    parser.add_argument('-e', dest='sendmail', action='store_true',
                        help='Send an email after processing a command')
    args = parser.parse_args()
    command = None
    if len(args.command) > 0:
        command = ''
        for par in args.command:
            if par is not None:
                command = command + par + ' '

    # Without arguments run as a daemon
    if command is None:
        # Prepare for running as daemon
        context = daemon.DaemonContext(
            working_directory='/tmp',
            umask=0o002,
            pidfile=lockfile.FileLock('/run/lock/heat.pid'),
        )

        # Redirect stdout and stderr to file
        log_file = open('heat.log', 'w+')
        context.stdout = log_file
        err_file = open('heat.err', 'w+')
        context.stderr = err_file

        # Open the daemon context and turn this program into a daemon
        with context:
            daemon_loop(args.timeout, args.sendmail)
    else:
        cc = ChronothermController()
        cc.init(args.sendmail, command)
        
        # Just handle the command on the commandline
        cc.handle_commands()
