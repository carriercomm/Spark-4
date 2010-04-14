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
import math
from datetime import datetime, timedelta
from collections import defaultdict
from spark.async import *
from spark.messaging import *
from spark.fileshare.tables import *

Units = [("KiB", 1024), ("MiB", 1024 * 1024), ("GiB", 1024 * 1024 * 1024)]
def formatSize(size):
    for unit, count in reversed(Units):
        if size >= count:
            return "%0.2f %s" % (size / float(count), unit)
    return "%d byte" % size

__all__ = ["Transfer"]
class Transfer(ProcessBase):
    def __init__(self):
        super(Transfer, self).__init__()
        self.stateChanged = EventSender("transfer-state-changed", int, int, basestring)
    
    def initState(self, state):
        """ Initialize the process state. """
        super(Transfer, self).initState(state)
        state.sessionPid = None
        state.reqID = None
        state.transferID = None
        state.direction = None
        state.transferState = None
        state.file = None
        state.path = None
        state.stream = None
        state.blockTable = None
        state.offset = None
        state.started = None
        state.ended = None
    
    def initPatterns(self, loop, state):
        """ Initialize the patterns used by the message loop. """
        super(Transfer, self).initPatterns(loop, state)
        loop.addHandlers(self,
            Command("init-transfer", int, int, None, int, int),
            Command("start-transfer"),
            Command("close-transfer"),
            Event("send-idle"),
            Event("remote-state-changed", basestring),
            Event("block-received", Block()))
    
    def cleanup(self, state):
        try:
            super(Transfer, self).cleanup(state)
        finally:
            self._closeFile(state)
            self._changeTransferState(state, "closed")
    
    def _closeFile(self, state):
        if state.stream:
            state.stream.close()
            state.stream = None
            state.logger.info("Closed file '%s'.", state.path) 
    
    def _changeTransferState(self, state, transferState):
        if state.transferState != transferState:
            state.logger.info("Transfer state changed from '%s' to '%s'",
                state.transferState, transferState)
            state.transferState = transferState
            self.stateChanged(state.transferID, state.direction, transferState)
    
    def onRemoteStateChanged(self, m, transferState, state):
        self._changeTransferState(state, transferState)
        if transferState == "active":
            self._startTransfer(state)
        elif transferState == "closed":
            self._closeTransfer(state)
    
    def doInitTransfer(self, m, transferID, direction, file, reqID, sessionPid, state):
        state.logger.info("Initializing transfer for file '%s'.", file.ID) 
        state.transferID = transferID
        state.direction = direction
        state.file = file
        state.reqID = reqID
        state.sessionPid = sessionPid
        state.blockSize = 1024
        state.receivedBlocks = 0
        state.completedSize = 0
        state.totalBlocks = int(math.ceil(float(file.size) / state.blockSize))
        if state.direction == UPLOAD:
            state.path = file.path
            state.stream = open(state.path, "rb")
            state.nextBlock = 0
        elif state.direction == DOWNLOAD:
            state.blockTable = defaultdict(bool)
            receiveDir = os.path.join(os.path.expanduser("~"), "Desktop")
            state.path = os.path.join(receiveDir, file.name)
            state.stream = open(state.path, "wb")
        state.logger.info("Opened file '%s'.", state.path)
        state.offset = 0
        state.transferState = "created"
        Process.send(sessionPid, Event("transfer-created",
            state.transferID, state.direction, state.file.ID, state.reqID))
        self._changeTransferState(state, "inactive")
    
    def doStartTransfer(self, m, state):
        self._startTransfer(state)
    
    def _startTransfer(self, state):
        state.logger.info("Starting transfer.")
        state.started = datetime.now()
        if state.direction == UPLOAD:
            self._changeTransferState(state, "active")
            self._sendFile(state)
    
    def _sendFile(self, state):
        if state.transferState != "active":
            return
        if state.nextBlock >= state.totalBlocks:
            self._transferComplete(state)
        else:
            # read the block
            blockData = state.stream.read(state.blockSize)
            state.offset += len(blockData)
            block = Block(state.transferID, state.nextBlock, blockData)
            state.nextBlock += 1
            state.completedSize += len(blockData)
            # send it
            Process.send(state.sessionPid, Command("send-block", block))
    
    def onSendIdle(self, m, state):
        if (state.direction == UPLOAD) and (state.started is not None):
            self._sendFile(state)
    
    def onBlockReceived(self, m, b, state):
        blockID = b.blockID
        if (not state.blockTable[blockID]) and (blockID < state.totalBlocks):
            fileOffset = blockID * state.blockSize
            if state.offset != fileOffset:
                state.stream.seek(fileOffset)
            state.stream.write(b.blockData)
            state.offset += len(b.blockData)
            state.blockTable[blockID] = True
            state.receivedBlocks += 1
            state.completedSize += len(b.blockData)
        if state.receivedBlocks == state.totalBlocks:
            self._transferComplete(state)
    
    def _transferComplete(self, state):
        state.ended = datetime.now()
        self._changeTransferState(state, "finished")
        state.logger.info("Transfer complete.")
        info = self._transferInfo(state)
        state.logger.info("Transfered %s in %s (%s/s).",
            formatSize(info.completedSize), info.duration, formatSize(info.averageSpeed))
    
    def _transferInfo(self, state):
        info = TransferInfo(state.transferID, state.direction, state.file.ID, self.pid)
        info.started = state.started
        info.ended = state.ended
        info.state = state.transferState
        info.completedSize = state.completedSize
        return info
    
    def doCloseTransfer(self, m, state):
        self._closeTransfer(state)
    
    def _closeTransfer(self, state):
        state.logger.info("Closing transfer.")
        self._closeFile(state)
        raise ProcessExit()