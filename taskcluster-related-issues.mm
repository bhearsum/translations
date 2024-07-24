---
title: Taskcluster related issues and relationships between them - remove issues entirely as they are fixed
---
%% Everything Taskcluster related from
%% https://github.com/mozilla/firefox-translations-training/issues/490
%% and https://github.com/mozilla/firefox-translations-training/issues/311
%% should be included here
%% remove items as they are fixed. anything with no arrows pointing to it
%% is ready to be worked on

flowchart LR
	466[466: <a href='https://github.com/mozilla/firefox-translations-training/issues/466'>automatically upload artifacts</a><br>status: in progress<br>assigned: bhearsum]
	618[618: <a href='https://github.com/mozilla/firefox-translations-training/issues/618'>bring snakepit machines online</a>]
	549[549: <a href='https://github.com/mozilla/firefox-translations-training/issues/549'>dns resolution issues</a><br>status: waiting to see if new images fix it<br>assigned: mboris]
	relops1307[relops1307: <a href='https://mozilla-hub.atlassian.net/browse/RELOPS-1307'>reimage snakepit machines</a><br>status: in progress<br>assigned: aerickson/yarik]
	1117[1117: <a href='https://github.com/mozilla/translations/issues/1117'>dockerization of gpu tasks causes GPUs to disappear intermittently</a><br>status: waiting for bhearsum to work on it<br>assigned: bhearsum]

	relops1307 --> 618
