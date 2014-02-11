"""
Copyright(C) 2014, Stamus Networks
Written by Eric Leblond <eleblond@stamus-networks.com>

This file is part of Scirius.

Scirius is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Scirius is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Scirius.  If not, see <http://www.gnu.org/licenses/>.
"""

from django.db import models
import urllib
import tempfile
import tarfile
import re
from datetime import datetime
import sys

# Create your models here.

class Source(models.Model):
    FETCH_METHOD = (
        ('http', 'HTTP URL'),
        ('https', 'HTTPS URL'),
        ('local', 'Upload'),
    )
    CONTENT_TYPE = (
        ('sigs', 'Signature files'),
        ('iprep', 'IP reputation files'),
        ('other', 'Other content'),
    )
    TMP_DIR = "/tmp/"

    name = models.CharField(max_length=100, unique = True)
    created_date = models.DateTimeField('date created')
    updated_date = models.DateTimeField('date updated', blank = True, null = True)
    uri = models.CharField(max_length=400)
    method = models.CharField(max_length=10, choices=FETCH_METHOD)
    datatype = models.CharField(max_length=10, choices=CONTENT_TYPE)

    editable = True
    # git repo where we store the physical thing
    # this allow to store the different versions
    # and to checkout the sources to a given version
    # for ruleset generation
    # Operations
    #  - Create
    #  - Delete
    #  - Update: only custom one
    #    Use method to get new files and commit them
    #    Create a new SourceAtVersion when there is a real update
    #    In case of upload: simply propose user upload form

    def __unicode__(self):
        return self.name

    def get_categories(self, tarfile):
        catname = re.compile("\/(.+)\.rules$")
        for member in tarfile.getmembers():
            if member.name.endswith('.rules'):
                match = catname.search(member.name)
                name = match.groups()[0]
                category = Category.objects.filter(source = self, name = name)
                if not category:
                    category = Category.objects.create(source = self,
                                            name = name, created_date = datetime.now(),
                                            filename = member.name)
                    category.save()
                    category.get_rules()
                else:
                    category[0].get_rules()
                # get rules in this category

    def update(self):
        if (self.method != 'http'):
            raise "Currently unsupported method"
        f = tempfile.NamedTemporaryFile(dir=self.TMP_DIR)
        urllib.urlretrieve (self.uri, f.name)
        # FIXME check file type
        # FIXME only dealing with tgz
        # extract file
        if (not tarfile.is_tarfile(f.name)):
            raise "Invalid tar file"
        # FIXME real work
        tfile = tarfile.open(fileobj=f)
        # FIXME get members
        tfile.extractall(path="/tmp/et")
        self.updated_date = datetime.now()
        self.save()
        # Now we must update SourceAtVersion for this source
        # or create it if needed
        # look for SourceAtVersion with name and HEAD
        # Update updated_date
        sversions  = SourceAtVersion.objects.filter(source = self, version = 'HEAD')
        if sversions:
            sversions[0].updated_date = self.updated_date
            sversions[0].save()
        else:
            sversion = SourceAtVersion.objects.create(source = self, version = 'HEAD',
                                                    updated_date = self.updated_date, git_version = 'HEAD')
            sversion.save()
        # Get categories
        self.get_categories(tfile)
        # Build rules list

    def get_absolute_url(self):
        from django.core.urlresolvers import reverse
        return reverse('source', args=[str(self.id)])


class SourceAtVersion(models.Model):
    source = models.ForeignKey(Source)
    # Sha1 or HEAD or tag
    version = models.CharField(max_length=42)
    git_version = models.CharField(max_length=42, default = 'HEAD')
    updated_date = models.DateTimeField('date updated', blank = True, default = datetime.now())

    def __unicode__(self):
        return str(self.source) + "@" + self.version

    def _get_name(self):
        return str(self)

    name = property(_get_name)

class Category(models.Model):
    name = models.CharField(max_length=100)
    filename = models.CharField(max_length=200)
    descr = models.CharField(max_length=400, blank = True)
    created_date = models.DateTimeField('date created', default = datetime.now())
    source = models.ForeignKey(Source)

    class Meta:
        verbose_name_plural = "categories"

    def __unicode__(self):
        return self.name

    def get_rules(self):
        # parse file
        getsid = re.compile("sid:(\d+)")
        getrev = re.compile("rev:(\d+)")
        getmsg = re.compile("msg:\"(.*?)\"")
        # FIXME not a fixed one
        TMP_DIR = "/tmp/et/"
        rfile = open(TMP_DIR + "/" + self.filename)
        for line in rfile.readlines():
            if line.startswith('#'):
                continue
            match = getsid.search(line)
            if not match:
                continue
            sid = match.groups()[0]
            match = getrev.search(line)
            if not match:
                continue
            rev = match.groups()[0]
            match = getmsg.search(line)
            if not match:
                msg = ""
            else:
                msg = match.groups()[0]
            # FIXME detect if nothing has changed to avoir rules reload
            rule = Rule.objects.filter(category = self, sid = sid)
            if rule:
                if rule[0].rev > rev:
                    rule[0].content = line
                    rule[0].rev = rev
                    rule[0].save()
            else:
                rule = Rule.objects.create(category = self, sid = sid,
                                    rev = rev, content = line, msg = msg)
                rule.save()
    def get_absolute_url(self):
        from django.core.urlresolvers import reverse
        return reverse('category', args=[str(self.id)])

class Rule(models.Model):
    category = models.ForeignKey(Category)
    msg = models.CharField(max_length=1000)
    state = models.BooleanField(default=True)
    sid = models.IntegerField(default=0, unique = True)
    rev = models.IntegerField(default=0)
    content = models.CharField(max_length=10000)

    hits = 0

    def __unicode__(self):
        return str(self.sid) + ":" + self.msg

# we should use django reversion to keep track of this one
# even if fixing HEAD may be complicated
class Ruleset(models.Model):
    name = models.CharField(max_length=100, unique = True)
    descr = models.CharField(max_length=400, blank = True)
    created_date = models.DateTimeField('date created')
    updated_date = models.DateTimeField('date updated', blank = True)

    editable = True

    # List of Source that can be used in the ruleset
    # It can be a specific version or HEAD if we want to use
    # latest available
    sources = models.ManyToManyField(SourceAtVersion)
    # List of Category selected in the ruleset
    categories = models.ManyToManyField(Category, blank = True)
    # List or Rules to suppressed from the Ruleset
    # Exported as suppression list in oinkmaster
    suppressed_rules = models.ManyToManyField(Rule, blank = True)
    # Operations
    # Creation:
    #  - define sources
    #  - define version
    #  - define categories
    #  - define suppressed rules
    # Delete
    # Copy
    #  - Specify new name
    # Refresh:
    #  - trigger update of sources
    #  - build new head
    # Update:
    #  - define version
    #  - update link
    # Generate appliance ruleset to directory:
    #  - get files from correct version exported to directory
    # Apply ruleset:
    #  - Tell Ansible to publish

    def __unicode__(self):
        return self.name

    def get_absolute_url(self):
        from django.core.urlresolvers import reverse
        return reverse('ruleset', args=[str(self.id)])

    def update(self):
        sourcesatversion = self.sources.all()
        for sourcesat in sourcesatversion:
            sourcesat.source.update()
        self.updated_date = datetime.now()
        self.save()

    def generate(self):
        rules = Rule.objects.filter(category__in = self.categories.all())
        # remove suppressed list
        rules = list(set(rules.all()) - set(self.suppressed_rules.all()))
        return rules

    def copy(self, name):
        orig_sources = self.sources.all()
        orig_categories = self.categories.all()
        orig_supp_rules = self.suppressed_rules.all()
        self.name = name
        self.pk = None
        self.id = None
        self.save()
        self.sources = orig_sources
        self.categories = orig_categories
        self.suppressed_rules = orig_supp_rules
        return self
