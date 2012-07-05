import re
from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import ugettext as _
from askbot.models.base import BaseQuerySetManager
from askbot import const
from askbot.conf import settings as askbot_settings

def delete_tags(tags):
    """deletes tags in the list"""
    tag_ids = [tag.id for tag in tags]
    Tag.objects.filter(id__in = tag_ids).delete()

def get_tags_by_names(tag_names):
    """returns query set of tags
    and a set of tag names that were not found
    """
    tags = Tag.objects.filter(name__in = tag_names)
    #if there are brand new tags, create them
    #and finalize the added tag list
    if tags.count() < len(tag_names):
        found_tag_names = set([tag.name for tag in tags])
        new_tag_names = set(tag_names) - found_tag_names
    else:
        new_tag_names = set()

    return tags, new_tag_names


def separate_unused_tags(tags):
    """returns two lists::
    * first where tags whose use counts are >0
    * second - with use counts == 0
    """
    used = list()
    unused = list()
    for tag in tags:
        if tag.used_count == 0:
            unused.append(tag)
        else:
            assert(tag.used_count > 0)
            used.append(tag)
    return used, unused

def tags_match_some_wildcard(tag_names, wildcard_tags):
    """Same as 
    :meth:`~askbot.models.tag.TagQuerySet.tags_match_some_wildcard`
    except it works on tag name strings
    """
    for tag_name in tag_names:
        for wildcard_tag in sorted(wildcard_tags):
            if tag_name.startswith(wildcard_tag[:-1]):
                return True
    return False

def get_mandatory_tags():
    """returns list of mandatory tags,
    or an empty list, if there aren't any"""
    from askbot.conf import settings as askbot_settings
    #TAG_SOURCE setting is hidden
    #and only is accessible via livesettings overrides
    if askbot_settings.TAG_SOURCE == 'category-tree':
        return []#hack: effectively we disable the mandatory tags feature
    else:
        #todo - in the future clean this up
        #we might need to have settings:
        #* prepopulated tags - json structure - either a flat list or a tree
        #  if structure is tree - then use some multilevel selector for choosing tags
        #  if it is a list - then make users click on tags to select them
        #* use prepopulated tags (boolean)
        #* tags are required
        #* regular users can create tags (boolean)
        #the category tree and the mandatory tag lists can be merged
        #into the same setting - and mandatory tags should use json
        #keep in mind that in the future multiword tags will be allowed
        raw_mandatory_tags = askbot_settings.MANDATORY_TAGS.strip()
        if len(raw_mandatory_tags) == 0:
            return []
        else:
            split_re = re.compile(const.TAG_SPLIT_REGEX)
            return split_re.split(raw_mandatory_tags)


class TagQuerySet(models.query.QuerySet):
    def get_valid_tags(self, page_size):
        tags = self.all().filter(deleted=False).exclude(used_count=0).order_by("-id")[:page_size]
        return tags

    def update_use_counts(self, tags):
        """Updates the given Tags with their current use counts."""
        for tag in tags:
            tag.used_count = tag.threads.count()
            tag.save()

    def mark_undeleted(self):
        """removes deleted(+at/by) marks"""
        self.update(#undelete them
            deleted = False,
            deleted_by = None,
            deleted_at = None
        )

    def tags_match_some_wildcard(self, wildcard_tags = None):
        """True if any one of the tags in the query set
        matches a wildcard

        :arg:`wildcard_tags` is an iterable of wildcard tag strings

        todo: refactor to use :func:`tags_match_some_wildcard`
        """
        for tag in self.all():
            for wildcard_tag in sorted(wildcard_tags):
                if tag.name.startswith(wildcard_tag[:-1]):
                    return True
        return False

    def get_by_wildcards(self, wildcards = None):
        """returns query set of tags that match the wildcard tags
        wildcard tag is guaranteed to end with an asterisk and has
        at least one character preceding the the asterisk. and there
        is only one asterisk in the entire name
        """
        if wildcards is None or len(wildcards) == 0:
            return self.none()
        first_tag = wildcards.pop()
        tag_filter = models.Q(name__startswith = first_tag[:-1])
        for next_tag in wildcards:
            tag_filter |= models.Q(name__startswith = next_tag[:-1])
        return self.filter(tag_filter)

    def get_related_to_search(self, threads, ignored_tag_names):
        """Returns at least tag names, along with use counts"""
        tags = self.filter(threads__in=threads).annotate(local_used_count=models.Count('id')).order_by('-local_used_count', 'name')
        if ignored_tag_names:
            tags = tags.exclude(name__in=ignored_tag_names)
        tags = tags.exclude(deleted = True)
        return list(tags[:50])


class TagManager(BaseQuerySetManager):
    """chainable custom filter query set manager
    for :class:``~askbot.models.Tag`` objects
    """
    def get_query_set(self):
        return TagQuerySet(self.model)

class GroupTagQuerySet(TagQuerySet):
    """Custom query set for the group"""

    def get_for_user(self, user = None):
        return self.filter(user_memberships__user = user)

    def get_all(self):
        return self.annotate(
            member_count = models.Count('user_memberships')
        ).filter(
            member_count__gt = 0
        )

    def get_by_name(self, group_name = None):
        return self.get(name = clean_group_name(group_name))


def clean_group_name(name):
    """group names allow spaces,
    tag names do not, so we use this method
    to replace spaces with dashes"""
    return re.sub('\s+', '-', name.strip())

class GroupTagManager(BaseQuerySetManager):
    """manager for group tags"""

    def get_query_set(self):
        return GroupTagQuerySet(self.model)

    def get_or_create(self, group_name = None, user = None):
        """creates a group tag or finds one, if exists"""
        #todo: here we might fill out the group profile

        #replace spaces with dashes
        group_name = clean_group_name(group_name)
        try:
            #iexact is important!!! b/c we don't want case variants
            #of tags
            tag = self.get(name__iexact = group_name)
        except self.model.DoesNotExist:
            tag = self.model(name = group_name, created_by = user)
            tag.save()
            from askbot.models.user import GroupProfile
            group_profile = GroupProfile(group_tag = tag)
            group_profile.save()
        return tag

class Tag(models.Model):
    name            = models.CharField(max_length=255, unique=True)
    created_by      = models.ForeignKey(User, related_name='created_tags')
    # Denormalised data
    used_count = models.PositiveIntegerField(default=0)

    deleted     = models.BooleanField(default=False)
    deleted_at  = models.DateTimeField(null=True, blank=True)
    deleted_by  = models.ForeignKey(User, null=True, blank=True, related_name='deleted_tags')

    tag_wiki = models.OneToOneField(
                                'Post',
                                null=True,
                                related_name = 'described_tag'
                            )

    objects = TagManager()
    group_tags = GroupTagManager()

    class Meta:
        app_label = 'askbot'
        db_table = u'tag'
        ordering = ('-used_count', 'name')

    def __unicode__(self):
        return self.name

class MarkedTag(models.Model):
    TAG_MARK_REASONS = (
        ('good', _('interesting')),
        ('bad', _('ignored')),
        ('subscribed', _('subscribed')),
    )
    tag = models.ForeignKey('Tag', related_name='user_selections')
    user = models.ForeignKey(User, related_name='tag_selections')
    reason = models.CharField(max_length=16, choices=TAG_MARK_REASONS)

    class Meta:
        app_label = 'askbot'

def get_groups():
    return Tag.group_tags.get_all()

def get_group_names():
    #todo: cache me
    return get_groups().values_list('name', flat = True)

class SuggestedTagManager(models.Manager):
    def create(self, tag_names = None, user = None, thread = None):
        """creates ``SuggestedTag`` records and saves them
        in the database"""
        suggested_tags = list()
        for tag_name in tag_names:
            #create new record
            suggested_tag = SuggestedTag(name = tag_name)
            suggested_tag.save()
            #add user and thread
            suggested_tag.users.add(user)
            suggested_tag.threads.add(thread)
            #add to the list that is to be returned
            suggested_tags.append(suggested_tag)

        #todo: stuff below will probably go after
        #tag moderation actions are implemented
        from askbot import mail
        from askbot.mail import messages
        body_text = messages.notify_admins_about_new_tags(
                                tags = tag_names,
                                user = user,
                                thread = thread
                            )
        site_name = askbot_settings.APP_SHORT_NAME
        subject_line = _('New tags added to %s') % site_name
        mail.mail_moderators(
            subject_line,
            body_text,
            headers = {'Reply-To': user.email}
        )

        msg = _(
            'Tags %s are new and will be submitted for the '
            'moderators approval'
        ) % ', '.join(tag_names)
        user.message_set.create(message = msg)

        return suggested_tags

class SuggestedTag(models.Model):
    """Suggested tag knows about who suggested it
    and in what thread"""
    name = models.CharField(
        max_length=255, unique=True, help_text = 'Name for the proposed tag'
    )
    used_count = models.PositiveIntegerField(default = 1)
    #todo: instead these can be associated with revisions
    #but the problem is that there would be too many joins
    #to pull out threads
    #if we used revisions, then we would not need to have
    #a separate "user" field
    threads = models.ManyToManyField('Thread')
    users = models.ManyToManyField(User)
    thread_ids = models.TextField(
        help_text = 'comma-separated list of thread ids'
    )

    objects = SuggestedTagManager()

    class Meta:
        app_label = 'askbot'
        ordering = ['-used_count', 'name']
