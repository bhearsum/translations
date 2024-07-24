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
	618[618: <a href='https://github.com/mozilla/firefox-translations-training/issues/618'>bring snakepit machines online</a>]
	relops1307[relops1307: <a href='https://mozilla-hub.atlassian.net/browse/RELOPS-1307'>reimage snakepit machines</a><br>status: in progress<br>assigned: aerickson/yarik]

	relops1307 --> 618
