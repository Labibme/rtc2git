import sys
import os

import sorter
import shell
from gitFunctions import Commiter
import shouter


class RTCInitializer:
    @staticmethod
    def initialize(config):
        RTCInitializer.loginandcollectstreams(config)
        workspace = WorkspaceHandler(config)
        if config.useexistingworkspace:
            shouter.shout("Use existing workspace to start migration")
            workspace.load()
        else:
            workspace.createandload(config.earlieststreamname, config.initialcomponentbaselines)

    @staticmethod
    def loginandcollectstreams(config):
        shell.execute("lscm login -r %s -u %s -P %s" % (config.repo, config.user, config.password))
        config.collectstreamuuids()


class WorkspaceHandler:
    def __init__(self, config):
        self.config = config
        self.workspace = config.workspace
        self.repo = config.repo

    def createandload(self, stream, componentbaselineentries=[], create=True):
        if create:
            shell.execute("lscm create workspace -r %s -s %s %s" % (self.config.repo, stream, self.workspace))
        if componentbaselineentries:
            self.setcomponentstobaseline(componentbaselineentries, stream)
        else:
            self.setcomponentstobaseline(ImportHandler(self.config).getcomponentbaselineentriesfromstream(stream),
                                         stream)
        self.load()

    def load(self):
        shouter.shout("Start (re)loading current workspace")
        shell.execute("lscm load -r %s %s --force" % (self.repo, self.workspace))
        shouter.shout("Load of workspace finished")

    def setcomponentstobaseline(self, componentbaselineentries, streamuuid):
        for entry in componentbaselineentries:
            shouter.shout("Set component '%s' to baseline '%s'" % (entry.componentname, entry.baselinename))

            replacecommand = "lscm set component -r %s -b %s %s stream %s %s --overwrite-uncommitted" % \
                             (self.repo, entry.baseline, self.workspace, streamuuid, entry.component)
            shell.execute(replacecommand)

    def setnewflowtargets(self, streamuuid):
        shouter.shout("Set new Flowtargets")
        if not self.hasflowtarget(streamuuid):
            shell.execute("lscm add flowtarget -r %s %s %s"
                          % (self.repo, self.workspace, streamuuid))
        shell.execute("lscm set flowtarget -r %s %s --default --current %s"
                      % (self.repo, self.workspace, streamuuid))

    def hasflowtarget(self, streamuuid):
        flowtargetlines = shell.getoutput("lscm --show-uuid y --show-alias n list flowtargets -r %s %s"
                                          % (self.repo, self.workspace))
        for flowtargetline in flowtargetlines:
            splittedinformationline = flowtargetline.split("\"")
            uuidpart = splittedinformationline[0].split(" ")
            flowtargetuuid = uuidpart[0].strip()[1:-1]
            if streamuuid in flowtargetuuid:
                return True
        return False

    def recreateoldestworkspace(self):
        self.createandload(self.config.earlieststreamname, self.config.initialcomponentbaselines, False)


class ImportHandler:
    def __init__(self, config):
        self.config = config

    def getcomponentbaselineentriesfromstream(self, stream):
        filename = self.config.getlogpath("StreamComponents_" + stream + ".txt")
        shell.execute(
            "lscm --show-alias n --show-uuid y list components -v -r " + self.config.repo + " " + stream, filename)
        componentbaselinesentries = []
        skippedfirstrow = False
        islinewithcomponent = 2
        component = ""
        baseline = ""
        componentname = ""
        baselinename = ""
        with open(filename, 'r') as file:
            for line in file:
                cleanedline = line.strip()
                if cleanedline:
                    if not skippedfirstrow:
                        skippedfirstrow = True
                        continue
                    splittedinformationline = line.split("\"")
                    uuidpart = splittedinformationline[0].split(" ")
                    if islinewithcomponent % 2 is 0:
                        component = uuidpart[3].strip()[1:-1]
                        componentname = splittedinformationline[1]
                    else:
                        baseline = uuidpart[5].strip()[1:-1]
                        baselinename = splittedinformationline[1]

                    if baseline and component:
                        componentbaselinesentries.append(
                            ComponentBaseLineEntry(component, baseline, componentname, baselinename, stream))
                        baseline = ""
                        component = ""
                        componentname = ""
                        baselinename = ""
                    islinewithcomponent += 1
        return componentbaselinesentries

    def acceptchangesintoworkspace(self, changeentries):
        git = Commiter
        amountofchanges = len(changeentries)
        shouter.shoutwithdate("Start accepting %s changesets" % amountofchanges)
        amountofacceptedchanges = 0
        for changeEntry in changeentries:
            amountofacceptedchanges += 1
            revision = changeEntry.revision
            acceptingmsg = "Accepting: " + changeEntry.comment + " (Date: " + changeEntry.date + ", Author: " \
                           + changeEntry.author + ", Revision: " + revision + ")"
            shouter.shout(acceptingmsg)
            acceptcommand = "lscm accept --overwrite-uncommitted --changes " + revision
            acceptedsuccesfully = shell.execute(acceptcommand, self.config.getlogpath("accept.txt"), "a") is 0
            if not acceptedsuccesfully:
                self.retryacceptincludingnextchangeset(changeEntry, changeentries, acceptcommand)

            shouter.shout("Accepted change %s/%s into working directory" % (amountofacceptedchanges, amountofchanges))
            git.addandcommit(changeEntry)

    def retryacceptincludingnextchangeset(self, changeentry, changeentries, acceptcommand):
        shouter.shout("Change wasnt succesfully accepted into workspace")
        nextindex = changeentries.index(changeentry) + 1
        successfull = False
        if nextindex is not len(changeentries):
            nextchangeentry = changeentries.__getitem__(nextindex)
            if changeentry.author == nextchangeentry.author:  # most likely merge changeset
                shouter.shout("Trying to accept next changeset (might be a solved merge-conflict)")
                acceptcommand += " " + nextchangeentry.revision
                successfull = shell.execute(acceptcommand, self.config.getlogpath("accept.txt"), "a") is 0

        if not successfull:
            shouter.shout("Last executed command: " + acceptcommand)
            sys.exit("Change wasnt succesfully accepted into workspace, please check the output and "
                     "rerun programm with resume")


    def getchangeentriesofstreamcomponents(self, componentbaselineentries):
        shouter.shout("Start comparing current workspace with baselines")
        missingchangeentries = {}

        for componentBaseLineEntry in componentbaselineentries:
            changeentries = self.getchangeentriesofbaseline(componentBaseLineEntry.baseline)
            for changeentry in changeentries:
                missingchangeentries[changeentry.revision] = changeentry
        return missingchangeentries

    def readhistory(self, componentbaselineentries):
        historyuuids = {}
        shouter.shout("Start reading history files")
        for componentBaseLineEntry in componentbaselineentries:
            history = self.gethistory(componentBaseLineEntry.component, componentBaseLineEntry.componentname,
                                      componentBaseLineEntry.stream)
            historyuuids[componentBaseLineEntry.component] = history
        return historyuuids

    @staticmethod
    def getchangeentriestoaccept(history, missingchangeentries):
        historywithchangeentryobject = {}
        for key in history.keys():
            currentuuids = history.get(key)
            changeentries = []
            for uuid in currentuuids:
                changeentry = missingchangeentries.get(uuid)
                if changeentry:
                    changeentries.append(changeentry)
            historywithchangeentryobject[key] = changeentries
        changeentriestoaccept = sorter.tosortedlist(historywithchangeentryobject)
        return changeentriestoaccept


    @staticmethod
    def getchangeentriesfromfile(outputfilename):
        informationseparator = "@@"
        changeentries = []

        with open(outputfilename, 'r') as file:
            for line in file:
                cleanedline = line.strip()
                if cleanedline:
                    splittedlines = cleanedline.split(informationseparator)
                    revisionwithbrackets = splittedlines[0].strip()
                    revision = revisionwithbrackets[1:-1]
                    author = splittedlines[1].strip()
                    email = splittedlines[2].strip()
                    comment = splittedlines[3].strip()
                    date = splittedlines[4].strip()
                    changeentries.append(ChangeEntry(revision, author, email, date, comment))
        return changeentries

    @staticmethod
    def getsimplehistoryfromfile(outputfilename):
        revisions = []
        if not os.path.isfile(outputfilename):
            shouter.shout("History file not found: " + outputfilename)
            shouter.shout("Skipping this part of history")
            return revisions

        with open(outputfilename, 'r') as file:
            for line in file:
                revisions.append(line.strip())
        revisions.reverse()  # to begin by the oldest
        return revisions

    def getchangeentriesofbaseline(self, baselinetocompare):
        return self.getchangeentriesbytypeandvalue("baseline", baselinetocompare)

    def getchangeentriesofstream(self, streamtocompare):
        shouter.shout("Start collecting changes since baseline creation")
        return self.getchangeentriesbytypeandvalue("stream", streamtocompare)

    def getchangeentriesbytypeandvalue(self, comparetype, value):
        dateformat = "yyyy-MM-dd HH:mm:ss"
        outputfilename = self.config.getlogpath("Compare_" + comparetype + "_" + value + ".txt")
        comparecommand = "lscm --show-alias n --show-uuid y compare ws %s %s %s -r %s -I sw -C @@{name}@@{email}@@ --flow-directions i -D @@\"%s\"@@" \
                         % (self.config.workspace, comparetype, value, self.config.repo, dateformat)
        shell.execute(comparecommand, outputfilename)
        return ImportHandler.getchangeentriesfromfile(outputfilename)

    def gethistory(self, componentuuid, componentname, streamuuid):
        outputfilename = self.config.getlogpath("History_" + componentname + ".txt")
        return ImportHandler.getsimplehistoryfromfile(outputfilename)


class ChangeEntry:
    def __init__(self, revision, author, email, date, comment):
        self.revision = revision
        self.author = author
        self.email = email
        self.date = date
        self.comment = comment

    def getgitauthor(self):
        authorrepresentation = "%s <%s>" % (self.author, self.email)
        return shell.quote(authorrepresentation)


class ComponentBaseLineEntry:
    def __init__(self, component, baseline, componentname, baselinename, streamuuid):
        self.component = component
        self.baseline = baseline
        self.componentname = componentname
        self.baselinename = baselinename
        self.stream = streamuuid
