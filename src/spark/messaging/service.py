# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 Pierre-André Saulais <pasaulais@free.fr>
#
# This file is part of the Spark File-transfer Tool.
#
# Spark is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Spark is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Spark; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import os
import socket
import logging
from collections import Sequence
from spark.async import *
from spark.messaging.protocol import *
from spark.messaging.messages import *

__all__ = ["TcpMessenger", "Service", "RequestMatcher"]

def toCamelCase(tag):
    """ Convert the tag to camel case (e.g. "create-transfer" becomes "createTransfer"). """
    words = tag.split("-")
    first = words.pop(0)
    words = [word.capitalize() for word in words]
    words.insert(0, first)
    return "".join(words)

def toPascalCase(tag):
    """ Convert the tag to Pascal case (e.g. "create-transfer" becomes "CreateTransfer"). """
    return "".join([word.capitalize() for word in tag.split("-")])

class TcpMessenger(object):
    # The messenger is not connected to any peer.
    DISCONNECTED = 0
    # The messenger is connected to a peer.
    CONNECTED = 1
    
    def __init__(self):
        self.pid = Process.spawn(self._entry, name="TcpMessenger")
        self.listening = EventSender("listening", None)
        self.connected = EventSender("connected", None)
        self.protocolNegociated = EventSender("protocol-negociated", basestring)
        self.disconnected = EventSender("disconnected")
    
    def connect(self, addr, senderPid=None):
        if not senderPid:
            senderPid = Process.current()
        Process.send(self.pid, Command("connect", addr, senderPid))
    
    def listen(self, addr, senderPid=None):
        if not senderPid:
            senderPid = Process.current()
        Process.send(self.pid, Command("listen", addr, senderPid))
    
    def accept(self, senderPid=None):
        if not senderPid:
            senderPid = Process.current()
        Process.send(self.pid, Command("accept", senderPid))
    
    def disconnect(self):
        Process.send(self.pid, Command("disconnect"))

    def send(self, message, senderPid=None):
        if not senderPid:
            senderPid = Process.current()
        Process.send(self.pid, Command("send", message, senderPid))
    
    def close(self):
        Process.try_send(self.pid, Command("stop"))
    
    def _entry(self):
        state = TcpProcessState()
        loop = MessageMatcher()
        # public messages
        loop.addPattern(Command("connect", None, int), self._connect)
        loop.addPattern(Command("listen", None, int), self._listen)
        loop.addPattern(Command("accept", int), self._accept)
        loop.addPattern(Command("disconnect"), self._disconnect)
        loop.addPattern(Command("send", None, int), self._send)
        loop.addPattern(Command("stop"), result=False)
        # internal messages
        loop.addPattern(Event("connected", None, None, bool), self._connected)
        loop.addPattern(Event("end-of-stream", int), self._endOfStream)
        try:
            loop.run(state)
        finally:
            self._closeConnection(state)
            self._closeServer(state)

    def _listen(self, m, state):
        if state.server:
            Process.send(m[3], Event("listen-error", "invalid-state"))
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        try:
            state.logger.info("Listening to incoming connections on %s.", repr(m[2]))
            server.bind(m[2])
            server.listen(1)
            self.listening(m[2])
        except socket.error as e:
            Process.send(m[3], Event("listen-error", e))
        else:
            state.server = server
    
    def _accept(self, m, state):
        if state.acceptReceiver:
            # we are already waiting for an incoming connection
            return
        elif (state.connState != TcpMessenger.DISCONNECTED) or not state.server:
            Process.send(m[2], Event("accept-error", "invalid-state"))
            return
        args = (state.server, m[2], Process.current())
        state.acceptReceiver = Process.spawn(self._waitForAccept, args, "TcpServer")

    def _waitForAccept(self, server, senderPid, messengerPid):
        log = Process.logger()
        log.info("Waiting for a connection.")
        try:
            conn, remoteAddr = server.accept()
        except socket.error as e:
            if e.errno == os.errno.EINVAL:
                # shutdown
                return
            else:
                Process.send(senderPid, Event("accept-error", e))
        else:
            self._startSession(conn, remoteAddr, False, senderPid, messengerPid)

    def _connect(self, m, state):
        if (state.connState != TcpMessenger.DISCONNECTED) or state.connectReceiver:
            Process.send(m[3], Event("connection-error", "invalid-state"))
            return
        args = (m[2], m[3], Process.current())
        state.connectReceiver = Process.spawn(self._waitForConnect, args, "TcpClient")
    
    def _waitForConnect(self, remoteAddr, senderPid, messengerPid):
        log = Process.logger()
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        conn.bind(("0.0.0.0", 0))
        log.info("Connecting to %s.", repr(remoteAddr))
        try:
            conn.connect(remoteAddr)
        except socket.error as e:
            Process.send(senderPid, Event("connection-error", e))
        else:
            self._startSession(conn, remoteAddr, True, senderPid, messengerPid)

    def _connected(self, m, state):
        conn, remoteAddr, initiating = m[2:5]
        if initiating:
            receiver = state.connectReceiver
            state.connectReceiver = None
        else:
            receiver = state.acceptReceiver
            state.acceptReceiver = None
        if state.receiver:
            state.logger.info("Dropping redundant connection to %s.", repr(remoteAddr))
            Process.send(receiver, Command("close"))
        else:
            # we're connected, update the process' state
            state.logger.info("Connected to %s.", repr(remoteAddr))
            state.connState = TcpMessenger.CONNECTED
            state.conn = conn
            state.remoteAddr = remoteAddr
            state.receiver = receiver
            stream = SocketWrapper(state.conn)
            self.connected(remoteAddr)
            # negociate the protocol to use for formatting messages
            name = negociateProtocol(stream, initiating)
            state.logger.info("Negociated protocol '%s'.", name)
            state.stream = stream
            state.protocol = name
            state.writer = messageWriter(stream, name)
            self.protocolNegociated(name)
            # start receiving messages
            Process.send(receiver, Command("receive", stream, name))
    
    def _startSession(self, conn, remoteAddr, initiating, senderPid, messengerPid):
        log = Process.logger()
        # notify the messenger process that we have established a connection
        Process.send(messengerPid, Event("connected", conn, remoteAddr, initiating))
        # the connection might have to be dropped (if we're already connected)
        resp = Process.receive()
        if not match(Command("receive", None, basestring), resp):
            try:
                conn.close()
            except Exception:
                log.exception("socket.close() failed")
            return
        # we can use this connection. start receiving messages
        reader = messageReader(resp[2], resp[3])
        try:
            while True:
                m = reader.read()
                if m is None:
                    break
                else:
                    log.info("Received message: '%s'." % str(m))
                    Process.send(senderPid, m)
        finally:
            Process.try_send(messengerPid, Event("end-of-stream", senderPid))
    
    def _endOfStream(self, m, state):
        self._closeConnection(state)
        self.disconnected()
    
    def _send(self, m, state):
        if (state.connState != TcpMessenger.CONNECTED) or state.protocol is None:
            Process.send(m[3], Event("send-error", "invalid-state"))
            return
        state.logger.info("Sending message: '%s'." % str(m[2]))
        state.writer.write(m[2])
    
    def _disconnect(self, m, state):
        self._closeConnection(state)
    
    def _closeConnection(self, state):
        if state.conn:
            state.logger.info("Disconnecting from %s." % repr(state.remoteAddr))
            state.stream = None
            state.protocol = None
            state.writer = None
            state.receiver = None
            # force threads blocked on recv (and send?) to return
            try:
                state.conn.shutdown(socket.SHUT_RDWR)
            except socket.error as e:
                if e.errno != os.errno.ENOTCONN:
                    raise
            except Exception:
                state.logger.exception("conn.shutdown() failed")
            # close the connection
            try:
                state.conn.close()
            except Exception:
                state.logger.exception("conn.close() failed")
            state.conn = None
            state.remoteAddr = None
            state.connState = TcpMessenger.DISCONNECTED
    
    def _closeServer(self, state):
        if state.server:
            # force threads blocked on accept() to return
            try:
                state.server.shutdown(socket.SHUT_RDWR)
            except Exception:
                state.logger.exception("server.shutdown() failed")
            try:
                state.server.close()
            except Exception:
                state.logger.exception("server.close() failed")
            state.server = None

class TcpProcessState(object):
    def __init__(self):
        self.connState = TcpMessenger.DISCONNECTED
        self.server = None
        self.conn = None
        self.remoteAddr = None
        self.recipient = None
        self.protocol = None
        self.stream = None
        self.writer = None
        self.receiver = None
        self.acceptReceiver = None
        self.connectReceiver = None
        self.logger = Process.logger()

class SocketWrapper(object):
    def __init__(self, sock):
        self.sock = sock
        self.write = sock.send
    
    def read(self, size):
        try:
            return self.sock.recv(size)
        except socket.error as e:
            if (e.errno == os.errno.ECONNRESET) or (e.errno == os.errno.EBADF):
                return ""
            else:
                raise

class Service(object):
    """ Base class for services that handle requests using messaging. """
    def __init__(self):
        self.connected = EventSender("connected")
        self.connectionError = EventSender("connection-error", None)
        self.disconnected = EventSender("disconnected")
    
    def initState(self, loop, state):
        state.bindAddr = None
        state.messenger = TcpMessenger()
        state.nextTransID = 1
    
    def initPatterns(self, loop, state):
        # messages received from TcpMessenger
        loop.suscribeTo(state.messenger.protocolNegociated, self.onProtocolNegociated)
        loop.suscribeTo(state.messenger.disconnected, self.onDisconnected)
        loop.addPattern(Event("connection-error", None), self.onConnectionError)
        # messages received from the caller
        loop.addPattern(Command("connect", None), self.connectMessenger)
        loop.addPattern(Command("bind", None), self.bindMessenger)
        loop.addPattern(Command("disconnect"), self.disconnectMessenger)
        loop.addPattern(Command("stop"), result=False)
        
    def run(self):
        """ Run the service. This method blocks until the service has finished executing. """
        loop = RequestMatcher()
        state = ProcessState()
        self.initState(loop, state)
        self.initPatterns(loop, state)
        try:
            loop.run(state)
        finally:
            state.messenger.close()
    
    def onConnectionError(self, m, state):
        self.connectionError(m[2])
    
    def onProtocolNegociated(self, m, state):
        self.connected()
    
    def onDisconnected(self, m, state):
        self.disconnected()
        if state.bindAddr:
            state.messenger.accept()
    
    def connectMessenger(self, m, state):
        state.messenger.connect(m[2])
    
    def bindMessenger(self, m, state):
        if not state.bindAddr:
            state.bindAddr = m[2]
            state.messenger.listen(state.bindAddr)
            state.messenger.accept()
    
    def disconnectMessenger(self, m, state):
        state.messenger.disconnect()
    
    def _newTransID(self, state):
        transID = state.nextTransID
        state.nextTransID += 1
        return transID
    
    def sendRequest(self, state, tag, *params):
        """ Send a request. """
        transID = self._newTransID(state)
        state.messenger.send(Request(tag, *params).withID(transID))
    
    def sendResponse(self, state, req, *params):
        """ Send a response to a request. """
        state.messenger.send(Response(req.tag, *params).withID(req.transID))
    
    def sendNotification(self, state, tag, *params):
        """ Send a notification. """
        transID = self._newTransID(state)
        state.messenger.send(Notification(tag, *params).withID(transID))

class RequestMatcher(MessageMatcher):
    def addRequestHandler(self, handler):
        """
        Add a predicate matching requests and invoking the relevant handler methods.
        For example a 'start-transfer' request would invoke the 'requestSartTransfer' method.
        """
        def matchRequestHandler(m, *args):
            if isinstance(m, Sequence) and len(m) >= 2 and match(('>', basestring), m[0:2]):
                method = self._findHandlerMethod(handler, m[1])
                if method:
                    method(m, *(m[2:] + args))
                    return True
            return False
        self.addPredicate(matchRequestHandler)
    
    def _findHandlerMethod(self, handler, tag):
        attrName = "request" + toPascalCase(tag)
        attr = getattr(handler, attrName, None)
        if hasattr(attr, "__call__"):
            return attr
        return None
    
    def addHandlers(self, handler, *patterns):
        """ Calls addHandler() for every pattern in the list. """
        for pattern in patterns:
            self.addHandler(pattern, handler)
    
    def addHandler(self, pattern, handler, result=True):
        """
        Add a rule that invokes the relevant handler methods when a message is matched.
        For example a 'start-transfer' request would invoke the 'requestSartTransfer' method.
        """
        if match((basestring, basestring), pattern[0:2]):
            prefix = pattern.__class__.__name__.lower()
            if prefix == "event":
                prefix = "on"
            attrName = prefix + toPascalCase(pattern[1])
            def invokeHandler(m, *args):
                attr = getattr(handler, attrName, None)
                if hasattr(attr, "__call__"):
                    attr(m, *(m[2:] + args))
                else:
                    Process.logger().error("Could not find handler method '%s' for message %s"
                        % (attrName, repr(m)))
            self.addPattern(pattern, invokeHandler, result)
        else:
            raise TypeError("pattern should be a message (sequence starting with two strings)")