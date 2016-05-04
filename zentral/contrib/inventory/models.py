from django.contrib.postgres.fields import JSONField
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models import Count, Q
from zentral.utils.mt_models import prepare_commit_tree, AbstractMTObject, MTObjectManager


class MetaBusinessUnitManager(models.Manager):
    def get_or_create_with_bu_key_and_name(self, key, name):
        try:
            mbu = self.get(businessunit__key=key)
        except MetaBusinessUnit.DoesNotExist:
            mbu = MetaBusinessUnit(name=name)
            mbu.save()
        return mbu

    def available_for_api_enrollment(self):
        return self.filter(businessunit__source__module='zentral.contrib.inventory')


class MetaBusinessUnit(models.Model):
    """The object to link the different BusinessUnits."""
    name = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MetaBusinessUnitManager()

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('name',)

    def get_absolute_url(self):
        return reverse('inventory:mbu_machines', args=(self.id,))

    def get_current_business_units(self):
        # !!! api enrollment business unit excluded !!!
        return BusinessUnit.objects.current().exclude(
            source__module='zentral.contrib.inventory').filter(meta_business_unit=self)

    def api_enrollment_business_units(self):
        return self.businessunit_set.filter(source__module='zentral.contrib.inventory').order_by('-id')

    def api_enrollment_enabled(self):
        return self.api_enrollment_business_units().count() > 0

    def create_enrollment_business_unit(self):
        reference = "MBU{}".format(self.id)
        b, created = BusinessUnit.objects.commit({'source': {'module': 'zentral.contrib.inventory',
                                                  'name': 'inventory'},
                                                  'reference': reference,
                                                  'name': reference},
                                                 meta_business_unit=self)
        if created:
            b.set_meta_business_unit(self)
        return b

    def has_machine(self, machine_serial_number):
        return self.businessunit_set.filter(machinesnapshot__machine__serial_number=machine_serial_number,
                                            machinesnapshot__mt_next__isnull=True).count() > 0

    def get_machine_count(self):
        qs = MetaBusinessUnit.objects.filter(pk=self.id)
        qs = qs.filter(businessunit__machinesnapshot__mt_next__isnull=True)
        qs = qs.annotate(num_msn=Count('businessunit__machinesnapshot__machine__serial_number', distinct=True))
        try:
            return qs[0].num_msn
        except IndexError:
            return 0

    def tags(self):
        tags = list(mbut.tag for mbut in self.metabusinessunittag_set.select_related('tag'))
        tags.sort(key=lambda t: (t.meta_business_unit is None, str(t).upper()))
        return tags


class SourceManager(MTObjectManager):
    def current_machine_group_sources(self):
        qs = self.filter(machinegroup__isnull=False,
                         machinegroup__machinesnapshot__mt_next__isnull=True)
        qs = qs.annotate(num_machine_groups=Count('machinegroup'))
        return qs.order_by('module', 'name')

    def current_business_unit_sources(self):
        qs = self.filter(businessunit__isnull=False,
                         businessunit__machinesnapshot__mt_next__isnull=True)
        qs = qs.annotate(num_business_units=Count('businessunit'))
        return qs.order_by('module', 'name')

    def current_machine_snapshot_sources(self):
        qs = self.filter(machinesnapshot__isnull=False,
                         machinesnapshot__mt_next__isnull=True)
        qs = qs.annotate(num_machine_snapshots=Count('machinesnapshot'))
        return qs.order_by('module', 'name')


class Source(AbstractMTObject):
    module = models.TextField()
    name = models.TextField()
    config = JSONField(blank=True, null=True)

    objects = SourceManager()

    def __str__(self):
        return self.name


class Link(AbstractMTObject):
    anchor_text = models.TextField()
    url = models.URLField()


class AbstractMachineGroupManager(MTObjectManager):
    def current(self):
        qs = self.filter(machinesnapshot__mt_next__isnull=True)
        return qs.distinct().select_related('source').order_by('source__module', 'name')


class AbstractMachineGroup(AbstractMTObject):
    source = models.ForeignKey(Source)
    reference = models.TextField()
    key = models.CharField(max_length=40, db_index=True)
    name = models.TextField()
    links = models.ManyToManyField(Link)

    objects = AbstractMachineGroupManager()
    mt_excluded_fields = ('key',)

    class Meta:
        abstract = True

    def generate_key(self):
        source_dict = self.source.serialize()
        source_dict.pop('name')
        data = {'source': source_dict,
                'reference': self.reference}
        prepare_commit_tree(data)
        return data['mt_hash']

    def save(self, *args, **kwargs):
        self.key = self.generate_key()
        super(AbstractMachineGroup, self).save()

    def get_short_key(self):
        return self.key[:8]


class BusinessUnit(AbstractMachineGroup):
    meta_business_unit = models.ForeignKey(MetaBusinessUnit)
    mt_excluded_fields = ('key', 'meta_business_unit')

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.key = self.generate_key()
        # get or create the corresponding MetaBusinessUnit
        # there must always be a MetaBusinessUnit for every BusinessUnit in the inventory
        # MetaBusinessUnits can be edited in the UI, not the BusinessUnits directly
        # Many BusinessUnits can be linked to a single MetaBusinessUnit to show that they are equivalent.
        mbu = kwargs.get('meta_business_unit', None)
        if not mbu:
            mbu = MetaBusinessUnit.objects.get_or_create_with_bu_key_and_name(self.key, self.name)
        self.meta_business_unit = mbu
        super(BusinessUnit, self).save(*args, **kwargs)

    def set_meta_business_unit(self, mbu):
        self.meta_business_unit = mbu
        super(BusinessUnit, self).save()

    def is_api_enrollment_business_unit(self):
        return self.source.module == "zentral.contrib.inventory"

    def get_name_display(self):
        if self.is_api_enrollment_business_unit():
            return "{} - API enrollment".format(self.meta_business_unit)
        else:
            return self.name


class MachineGroup(AbstractMachineGroup):
    machine_links = models.ManyToManyField(Link, related_name="+")  # tmpl for links to machine in a group


class Machine(AbstractMTObject):
    serial_number = models.TextField(unique=True)


class OSVersion(AbstractMTObject):
    name = models.TextField(blank=True, null=True)
    major = models.PositiveIntegerField()
    minor = models.PositiveIntegerField()
    patch = models.PositiveIntegerField(blank=True, null=True)
    build = models.TextField(blank=True, null=True)

    def __str__(self):
        l = [".".join((str(i) for i in (self.major, self.minor, self.patch) if i is not None))]
        if self.name:
            l.insert(0, self.name)
        if self.build:
            l.append("({})".format(self.build))
        return " ".join(l)


class SystemInfo(AbstractMTObject):
    computer_name = models.TextField(blank=True, null=True)
    hostname = models.TextField(blank=True, null=True)
    hardware_model = models.TextField(blank=True, null=True)
    hardware_serial = models.TextField(blank=True, null=True)
    cpu_type = models.TextField(blank=True, null=True)
    cpu_subtype = models.TextField(blank=True, null=True)
    cpu_brand = models.TextField(blank=True, null=True)
    cpu_physical_cores = models.PositiveIntegerField(blank=True, null=True)
    cpu_logical_cores = models.PositiveIntegerField(blank=True, null=True)
    physical_memory = models.BigIntegerField(blank=True, null=True)


class Certificate(AbstractMTObject):
    common_name = models.TextField()
    organization = models.TextField(blank=True, null=True)
    organizational_unit = models.TextField(blank=True, null=True)
    sha_1 = models.CharField(max_length=40)
    sha_256 = models.CharField(max_length=64, db_index=True)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    signed_by = models.ForeignKey('self', blank=True, null=True)


class OSXApp(AbstractMTObject):
    bundle_id = models.TextField(db_index=True, blank=True, null=True)
    bundle_name = models.TextField(db_index=True, blank=True, null=True)
    bundle_version = models.TextField(blank=True, null=True)
    bundle_version_str = models.TextField(blank=True, null=True)


class OSXAppInstance(AbstractMTObject):
    app = models.ForeignKey(OSXApp)
    bundle_path = models.TextField(blank=True, null=True)
    path = models.TextField(blank=True, null=True)
    sha_1 = models.CharField(max_length=40, blank=True, null=True)
    sha_256 = models.CharField(max_length=64, db_index=True, blank=True, null=True)
    type = models.TextField(blank=True, null=True)
    signed_by = models.ForeignKey(Certificate, blank=True, null=True)


class TeamViewer(AbstractMTObject):
    teamviewer_id = models.TextField(blank=False, null=False)
    release = models.TextField(blank=True, null=True)
    unattended = models.NullBooleanField(blank=True, null=True)


class MachineSnapshotManager(MTObjectManager):
    def commit(self, tree):
        obj, created = super().commit(tree, current=True)
        if created:
            self.filter(source=obj.source,
                        machine__serial_number=obj.machine.serial_number,
                        mt_next__isnull=True).exclude(pk=obj.id).update(mt_next=obj)
        return obj, created

    def current(self):
        return self.select_related('machine',
                                   'business_unit__meta_business_unit',
                                   'os_version',
                                   'system_info',
                                   'teamviewer').filter(mt_next__isnull=True)

    def get_current_count(self):
        result = self.current().aggregate(Count('machine__serial_number', distinct=True))
        return result['machine__serial_number__count']


class MachineSnapshot(AbstractMTObject):
    source = models.ForeignKey(Source)
    reference = models.TextField(blank=True, null=True)
    machine = models.ForeignKey(Machine)
    links = models.ManyToManyField(Link)
    business_unit = models.ForeignKey(BusinessUnit, blank=True, null=True)
    groups = models.ManyToManyField(MachineGroup)
    os_version = models.ForeignKey(OSVersion, blank=True, null=True)
    system_info = models.ForeignKey(SystemInfo, blank=True, null=True)
    osx_app_instances = models.ManyToManyField(OSXAppInstance)
    teamviewer = models.ForeignKey(TeamViewer, blank=True, null=True)
    mt_next = models.OneToOneField('self', blank=True, null=True, related_name="mt_previous")

    objects = MachineSnapshotManager()
    mt_excluded_fields = ('mt_next',)

    def update_diff(self):
        try:
            previous_snapshot = self.mt_previous
        except MachineSnapshot.DoesNotExist:
            return None
        else:
            return self.diff(previous_snapshot)

    def get_machine_str(self):
        if self.system_info and self.system_info.computer_name:
            return self.system_info.computer_name
        elif self.machine:
            return self.machine.serial_number
        elif self.reference:
            return self.reference
        else:
            return "{} #{}".format(self.source, self.id)

    def groups_with_links(self):
        for group in self.groups.prefetch_related('links', 'machine_links').all():
            ll = []
            for link in group.links.all():
                ll.append((link.url, link.anchor_text))
            for link in group.machine_links.all():
                url = link.url
                url = url.replace('%MACHINE_SNAPSHOT_REFERENCE%', self.reference)
                ll.append((url, link.anchor_text))
            yield group, ll


class TagManager(models.Manager):
    def available_for_meta_business_unit(self, meta_business_unit):
        return self.filter(Q(meta_business_unit=meta_business_unit) | Q(meta_business_unit__isnull=True))


class Tag(models.Model):
    meta_business_unit = models.ForeignKey(MetaBusinessUnit, blank=True, null=True)
    name = models.TextField()
    color = models.CharField(max_length=6, default="FFFFFF")

    objects = TagManager()

    def __str__(self):
        if self.meta_business_unit:
            return "{}/{}".format(self.meta_business_unit, self.name)
        else:
            return self.name


class MachineTag(models.Model):
    serial_number = models.TextField()
    tag = models.ForeignKey(Tag)

    class Meta:
        unique_together = (('serial_number', 'tag'),)


class MetaBusinessUnitTag(models.Model):
    meta_business_unit = models.ForeignKey(MetaBusinessUnit)
    tag = models.ForeignKey(Tag)


class Machine(object):
    """Simplified access to the ms."""
    def __init__(self, serial_number, snapshots=None):
        self.serial_number = serial_number
        if snapshots is None:
            self.snapshots = list(MachineSnapshot.objects.current().filter(machine__serial_number=serial_number))
        else:
            self.snapshots = snapshots

    def computer_name(self):
        for ms in self.snapshots:
            if ms.system_info and ms.system_info.computer_name:
                return ms.system_info.computer_name

    def business_units(self, include_api_enrollment_business_unit=False):
        bu_l = []
        for ms in self.snapshots:
            if (ms.business_unit and
                (include_api_enrollment_business_unit or
                 not ms.business_unit.is_api_enrollment_business_unit())):
                bu_l.append(ms.business_unit)
        return bu_l

    def meta_business_units(self):
        return set([bu.meta_business_unit for bu in self.business_units()])

    def snapshots_with_osx_app_instances(self):
        return list(ms for ms in self.snapshots if ms.osx_app_instances.count())

    def tags_with_types(self):
        tags = [('machine', mt.tag)
                for mt in MachineTag.objects.select_related('tag').filter(
                    serial_number=self.serial_number
                )]
        tags.extend(('meta_business_unit', mbut.tag)
                    for mbut in MetaBusinessUnitTag.objects.filter(meta_business_unit__in=self.meta_business_units()))
        tags.sort(key=lambda t: (t[1].meta_business_unit is None, str(t[1]).upper()))
        return tags

    def tags(self):
        tags = list({t[1] for t in self.tags_with_types()})
        tags.sort(key=lambda t: (t.meta_business_unit is None, str(t).upper()))
        return tags

    def available_tags(self):
        # tags w/o mbu or w mbu where this machine is and that this machine does not have yet
        tags = set([])
        for meta_business_unit in self.meta_business_units():
            tags.update(Tag.objects.available_for_meta_business_unit(meta_business_unit))
        tags = list(tags.difference(self.tags()))
        tags.sort(key=lambda t: (t.meta_business_unit is None, str(t).upper()))
        return tags
