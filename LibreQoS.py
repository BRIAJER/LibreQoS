# Copyright (C) 2020  Robert Chacón
# This file is part of LibreQoS.
#
# LibreQoS is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# LibreQoS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with LibreQoS.  If not, see <http://www.gnu.org/licenses/>.
#
#            _     _ _               ___       ____  
#           | |   (_) |__  _ __ ___ / _ \  ___/ ___| 
#           | |   | | '_ \| '__/ _ \ | | |/ _ \___ \ 
#           | |___| | |_) | | |  __/ |_| | (_) |__) |
#           |_____|_|_.__/|_|  \___|\__\_\\___/____/
#                          v.0.71-alpha
#
import random
import logging
import os
import json
import subprocess
from subprocess import PIPE
import ipaddress
import time
from datetime import date, datetime
from UNMS_Integration import pullUNMSDevices
from LibreNMS_Integration import pullLibreNMSDevices
from ispConfig import fqOrCAKE, pipeBandwidthCapacityMbps, interfaceA, interfaceB, addTheseSubnets, enableActualShellCommands, runShellCommandsAsSudo, importFromUNMS, importFromLibreNMS

def shell(inputCommand):
	if enableActualShellCommands:
		if runShellCommandsAsSudo:
			inputCommand = 'sudo ' + inputCommand
		inputCommandSplit = inputCommand.split(' ')
		print(inputCommand)
		result = subprocess.run(inputCommandSplit, stdout=subprocess.PIPE)
		print(result.stdout)
	else:
		print(inputCommand)
	
def clearPriorSettings(interfaceA, interfaceB):
	shell('tc filter delete dev ' + interfaceA)
	shell('tc filter delete dev ' + interfaceA + ' root')
	shell('tc qdisc delete dev ' + interfaceA)
	shell('tc qdisc delete dev ' + interfaceA + ' root')
	shell('tc filter delete dev ' + interfaceB)
	shell('tc filter delete dev ' + interfaceB + ' root')
	shell('tc qdisc delete dev ' + interfaceB)
	shell('tc qdisc delete dev ' + interfaceB + ' root') 

def getHashList():
	twoDigitHash = []
	letters = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z']
	for i in range(10):
		for x in range(26):
			twoDigitHash.append(str(i) + letters[x])
	return twoDigitHash

def addCustomersBySubnet(inputBlock):
	addTheseSubnets, existingShapableDevices = inputBlock
	customersToAdd = []
	for subnetItem in addTheseSubnets:
		ipcidr, downloadMbps, uploadMbps = subnetItem
		theseHosts = list(ipaddress.ip_network(ipcidr).hosts())
		for host in theseHosts:
			deviceIP = str(host)
			alreadyAssigned = False
			for device in existingShapableDevices:
				if deviceIP == device['identification']['ipAddr']:
					alreadyAssigned = True
			if not alreadyAssigned:
				thisShapedDevice = {
					"identification": {
					  "name": None,
					  "hostname": None,
					  "ipAddr": deviceIP,
					  "mac": None,
					  "model": None,
					  "modelName": None,
					  "unmsSiteID": None,
					  "libreNMSSiteID": None
					},
					"qos": {
					  "downloadMbps": downloadMbps,
					  "uploadMbps": uploadMbps,
					  "accessPoint": None
					},
				}
				customersToAdd.append(thisShapedDevice)
	return customersToAdd

def refreshShapers():
	#Clients
	shapableDevices = []
	
	#Bring in clients from UNMS or LibreNMS if enabled
	if importFromUNMS:
		shapableDevices.extend(pullUNMSDevices())
	if importFromLibreNMS:
		shapableDevices.extend(pullLibreNMSDevices())

	#Add customers by subnet. Will not replace those that already exist
	if addTheseSubnets:
		shapableDevices.extend(addCustomersBySubnet((addTheseSubnets, shapableDevices)))

	#Categorize Clients By IPv4 /16
	listOfSlash16SubnetsInvolved = []
	shapableDevicesListWithSubnet = []
	for device in shapableDevices:
		ipAddr = device['identification']['ipAddr']
		dec1, dec2, dec3, dec4 = ipAddr.split('.')
		slash16 = dec1 + '.' + dec2 + '.0.0'
		if slash16 not in listOfSlash16SubnetsInvolved:
			listOfSlash16SubnetsInvolved.append(slash16)
		shapableDevicesListWithSubnet.append((ipAddr))
	
	#Clear Prior Configs
	clearPriorSettings(interfaceA, interfaceB)
	
	#InterfaceA
	parentIDFirstPart = 1
	srcOrDst = 'dst'
	classIDCounter = 101
	hashIDCounter = parentIDFirstPart + 1
	shell('tc qdisc replace dev ' + interfaceA + ' root handle ' + str(parentIDFirstPart) + ': htb default 1') 
	shell('tc class add dev ' + interfaceA + ' parent ' + str(parentIDFirstPart) + ': classid ' + str(parentIDFirstPart) + ':1 htb rate '+ str(pipeBandwidthCapacityMbps) + 'mbit')
	for slash16 in listOfSlash16SubnetsInvolved:
		#X.X.0.0
		thisSlash16Dec1 = slash16.split('.')[0]
		thisSlash16Dec2 = slash16.split('.')[1]
		groupedCustomers = []	
		for i in range(256):
			tempList = []
			for ipAddr in shapableDevicesListWithSubnet:
				dec1, dec2, dec3, dec4 = ipAddr.split('.')
				if (dec1 == thisSlash16Dec1) and (dec2 == thisSlash16Dec2) and (dec4 == str(i)):
					tempList.append(ipAddr)
			if len(tempList) > 0:
				groupedCustomers.append(tempList)
		shell('tc filter add dev ' + interfaceA + ' parent ' + str(parentIDFirstPart) + ': prio 5 u32')
		shell('tc filter add dev ' + interfaceA + ' parent ' + str(parentIDFirstPart) + ': prio 5 handle ' + str(hashIDCounter) + ': u32 divisor 256')
		thirdDigitCounter = 0
		handleIDSecond = 1
		while thirdDigitCounter <= 255:	
			if len(groupedCustomers) > 0:
				currentIPList = groupedCustomers.pop()
				tempHashList = getHashList()
				for ipAddr in currentIPList:
					for device in shapableDevices:
						if device['identification']['ipAddr'] == ipAddr:
							downloadSpeed = device['qos']['downloadMbps']
							uploadSpeed = device['qos']['uploadMbps']
					dec1, dec2, dec3, dec4 = ipAddr.split('.')
					twoDigitHashString = hex(int(dec4)).replace('0x','')
					shell('tc class add dev ' + interfaceA + ' parent ' + str(parentIDFirstPart) + ':1 classid ' + str(parentIDFirstPart) + ':' + str(classIDCounter) + ' htb rate '+ str(downloadSpeed) + 'mbit ceil '+ str(downloadSpeed) + 'mbit prio 3') 
					shell('tc qdisc add dev ' + interfaceA + ' parent ' + str(parentIDFirstPart) + ':' + str(classIDCounter) + ' ' + fqOrCAKE)
					shell('tc filter add dev ' + interfaceA + ' parent ' + str(parentIDFirstPart) + ': prio 5 u32 ht ' + str(hashIDCounter) + ':' + twoDigitHashString + ' match ip ' + srcOrDst + ' ' + ipAddr + ' flowid ' + str(parentIDFirstPart) + ':' + str(classIDCounter))
					deviceQDiscID = str(parentIDFirstPart) + ':' + str(classIDCounter)
					for device in shapableDevices:
						if device['identification']['ipAddr'] == ipAddr:
							if srcOrDst == 'src':
								qdiscDict ={'qDiscSrc': deviceQDiscID}
							elif srcOrDst == 'dst':
								qdiscDict ={'qDiscDst': deviceQDiscID}
							device['identification'].update(qdiscDict)
					classIDCounter += 1
			thirdDigitCounter += 1
		if (srcOrDst == 'dst'):
			startPointForHash = '16' #Position of dst-address in IP header
		elif  (srcOrDst == 'src'):
			startPointForHash = '12' #Position of src-address in IP header
		shell('tc filter add dev ' + interfaceA + ' parent ' + str(parentIDFirstPart) + ': prio 5 u32 ht 800:: match ip ' + srcOrDst + ' '+ thisSlash16Dec1 + '.' + thisSlash16Dec2 + '.0.0/16 hashkey mask 0x000000ff at ' + startPointForHash + ' link ' + str(hashIDCounter) + ':')
		hashIDCounter += 1
	
	#InterfaceB
	parentIDFirstPart = hashIDCounter + 1
	hashIDCounter = parentIDFirstPart + 1
	srcOrDst = 'src'
	shell('tc qdisc replace dev ' + interfaceB + ' root handle ' + str(parentIDFirstPart) + ': htb default 1') 
	shell('tc class add dev ' + interfaceB + ' parent ' + str(parentIDFirstPart) + ': classid ' + str(parentIDFirstPart) + ':1 htb rate '+ str(pipeBandwidthCapacityMbps) + 'mbit')
	for slash16 in listOfSlash16SubnetsInvolved:
		#X.X.0.0
		thisSlash16Dec1 = slash16.split('.')[0]
		thisSlash16Dec2 = slash16.split('.')[1]
		groupedCustomers = []	
		for i in range(256):
			tempList = []
			for ipAddr in shapableDevicesListWithSubnet:
				dec1, dec2, dec3, dec4 = ipAddr.split('.')
				if (dec1 == thisSlash16Dec1) and (dec2 == thisSlash16Dec2) and (dec4 == str(i)):
					tempList.append(ipAddr)
			if len(tempList) > 0:
				groupedCustomers.append(tempList)
		shell('tc filter add dev ' + interfaceB + ' parent ' + str(parentIDFirstPart) + ': prio 5 u32')
		shell('tc filter add dev ' + interfaceB + ' parent ' + str(parentIDFirstPart) + ': prio 5 handle ' + str(hashIDCounter) + ': u32 divisor 256')
		thirdDigitCounter = 0
		handleIDSecond = 1
		while thirdDigitCounter <= 255:	
			if len(groupedCustomers) > 0:
				currentIPList = groupedCustomers.pop()
				tempHashList = getHashList()
				for ipAddr in currentIPList:
					for device in shapableDevices:
						if device['identification']['ipAddr'] == ipAddr:
							downloadSpeed = device['qos']['downloadMbps']
							uploadSpeed = device['qos']['uploadMbps']
					dec1, dec2, dec3, dec4 = ipAddr.split('.')
					twoDigitHashString = hex(int(dec4)).replace('0x','')
					shell('tc class add dev ' + interfaceB + ' parent ' + str(parentIDFirstPart) + ':1 classid ' + str(parentIDFirstPart) + ':' + str(classIDCounter) + ' htb rate '+ str(uploadSpeed) + 'mbit ceil '+ str(uploadSpeed) + 'mbit prio 3') 
					shell('tc qdisc add dev ' + interfaceB + ' parent ' + str(parentIDFirstPart) + ':' + str(classIDCounter) + ' ' + fqOrCAKE)
					shell('tc filter add dev ' + interfaceB + ' parent ' + str(parentIDFirstPart) + ': prio 5 u32 ht ' + str(hashIDCounter) + ':' + twoDigitHashString + ' match ip ' + srcOrDst + ' ' + ipAddr + ' flowid ' + str(parentIDFirstPart) + ':' + str(classIDCounter))
					deviceQDiscID = str(parentIDFirstPart) + ':' + str(classIDCounter)
					for device in shapableDevices:
						if device['identification']['ipAddr'] == ipAddr:
							if srcOrDst == 'src':
								qdiscDict ={'qDiscSrc': deviceQDiscID}
							elif srcOrDst == 'dst':
								qdiscDict ={'qDiscDst': deviceQDiscID}
							device['identification'].update(qdiscDict)
					classIDCounter += 1
			thirdDigitCounter += 1
		if (srcOrDst == 'dst'):
			startPointForHash = '16' #Position of dst-address in IP header
		elif  (srcOrDst == 'src'):
			startPointForHash = '12' #Position of src-address in IP header
		shell('tc filter add dev ' + interfaceB + ' parent ' + str(parentIDFirstPart) + ': prio 5 u32 ht 800:: match ip ' + srcOrDst + ' '+ thisSlash16Dec1 + '.' + thisSlash16Dec2 + '.0.0/16 hashkey mask 0x000000ff at ' + startPointForHash + ' link ' + str(hashIDCounter) + ':')
		hashIDCounter += 1
	
	#Save shapableDevices to file to allow for debugging and statistics runs
	with open('shapableDevices.json', 'w') as outfile:
		json.dump(shapableDevices, outfile)
	
	#Recap and log
	logging.basicConfig(level=logging.INFO, filename="log", filemode="a+",	format="%(asctime)-15s %(levelname)-8s %(message)s")
	for device in shapableDevices:
		ipAddr = device['identification']['ipAddr']
		hostname = device['identification']['hostname']
		downloadSpeed = str(device['qos']['downloadMbps'])
		uploadSpeed = str(device['qos']['uploadMbps'])
		if hostname:
			recap = "Applied rate limiting of " + downloadSpeed + " down " + uploadSpeed + " up to device " + hostname
		else:
			recap = "Applied rate limiting of " + downloadSpeed + " down " + uploadSpeed + " up to device " + ipAddr
		logging.info(recap)
		print(recap)
	totalChanges = str(len(shapableDevices)) + " device rules (" + str(len(shapableDevices)*2) + " filter rules) were applied this round"
	logging.info(totalChanges)
	print(totalChanges)
	
	#Done
	currentTimeString = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
	print("Successful run completed on " + currentTimeString)

if __name__ == '__main__':
	refreshShapers()
	print("Program complete")
