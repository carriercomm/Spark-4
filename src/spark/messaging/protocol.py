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

from async import Future
from parser import MessageReader
from messages import MessageWriter

__all__ = ["messageReader", "messageWriter", "negociateProtocol", "Supported"]

Supported = frozenset(["SPARKv1"])

def messageReader(file, buffer=4096):
    return MessageReader(file, buffer)

def messageWriter(file):
    return MessageWriter(file)

def negociateProtocol(f, initiating):
    """
    Negociate a protocol with the remote peer, using the file for exchanging messages.
    'initiating' indicates whether the local user initiated the connection or not.
    """
    n = Negociator(f, "\r\n")
    return n.negociate(initiating)

class Negociator(object):
    def __init__(self, file, newline):
        self.file = file
        self.newline = newline
    
    def negociate(self, initiating):
        cont = Future()
        if initiating:
            def step2(remoteChoice):
                if remoteChoice not in Supported:
                    raise ValueError("Protocol '%s' is not supported" % remoteChoice)
                self.writeProtocol(remoteChoice).after(cont, remoteChoice)
            def step1():
                self.readProtocol().fork(step2, cont)
            self.writeSupportedProtocols().fork(step1, cont)
        else:
            def step3(remoteChoice, choice):
                if remoteChoice != choice:
                    cont.failed(ValueError("The remote peer chose another protocol: '%s' (was '%s')" % (remoteChoice, choice)))
                cont.completed(remoteChoice)
            def step2(choice):
                self.readProtocol().fork(step3, cont, choice)
            def step1(proposed):
                choice = self.chooseProtocol(proposed)
                self.writeProtocol(choice).fork(step2, cont, choice)
            self.readSupportedProtocols().fork(step1, cont)
        return cont
    
    def chooseProtocol(self, proposedNames):
        for name in proposedNames:
            if name in Supported:
                return name
        raise ValueError("No protocol in the proposed list is supported")
    
    def writeSupportedProtocols(self):
        data = "supports %s%s" % (" ".join(Supported), self.newline)
        try:
            self.file.write(data)
            return Future.done()
        except:
            return Future.error()
    
    def writeProtocol(self, name):
        data = "protocol %s%s" % (name, self.newline)
        try:
            self.file.write(data)
            return Future.done()
        except:
            return Future.error()
    
    def readSupportedProtocols(self):
        cont = Future()
        self.readLine().after(self.parseSupportedProtocol, cont)
        return cont
    
    def parseSupportedProtocol(self, prev, cont):
        chunks = prev.result.split(" ")
        if chunks[0] != "supports":
            cont.failed(ValueError("Exepected '%s', read '%s'" % ("supports", chunks[0])))
        elif len(chunks) < 2:
            cont.failed(ValueError("Expected at least one protocol name"))
        else:
            cont.completed(chunks[1:])
    
    def readProtocol(self):
        cont = Future()
        self.readLine().after(self.parseProtocol, cont)
        return cont
    
    def parseProtocol(self, prev, cont):
        chunks = prev.result.split(" ")
        if chunks[0] != "protocol":
            cont.failed(ValueError("Exepected '%s', read '%s'" % ("protocol", chunks[0])))
        elif len(chunks) < 2:
            cont.failed(ValueError("Expected a protocol name"))
        else:
            cont.completed(chunks[1])
    
    def readLine(self):
        data = []
        matched = 0
        total = len(self.newline)
        c = self.file.read(1)
        while len(c) == 1:
            if c == self.newline[matched]:
                matched += 1
                if matched == total:
                    break
            else:
                data.append(c)
            c = self.file.read(1)
        if len(c) == 0:
            return Future.error(EOFError())
        else:
            return Future.done("".join(data))