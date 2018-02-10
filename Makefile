PACKAGE = efb_fb_messenger_slave

gettext:
	find ./$(PACKAGE) -iname "*.py" | xargs xgettext -o ./$(PACKAGE)/locale/$(PACKAGE).pot

crowdin: gettext
	crowdin push

crowdin-pull:
	crowdin pull

publish:
	python setup.py sdist bdist_wheel upload --sign --show-response

pre-release: crowdin crowdin-pull
