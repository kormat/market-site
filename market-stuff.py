#!/usr/bin/env python3

# This requires a sqlite database dump from
# http://pozniak.pl/dbdump/ody110-sqlite3-v1.db.bz2

# Something that I ran to extract module lists from copy pasted forum posts:
# cat new-list | tr '[' '\n' | tr ',' '\n' | sed -e 's/^[ \t]*//' | sed 's/ x.$//' | sort | uniq | ./market-stuff.py --filter

from collections import namedtuple
from xml.dom.minidom import parseString
import collections
import email
import sqlite3
import sys
import urllib.request as urlreq

CHUNK_SIZE = 100
ITEM_LIST = 'items'
EVECENTRAL_HOURS = 48
MARKET_HUB = 'Jita'

ColumnProperties = namedtuple('ColumnProperties', ['display_name', 'is_numeric'])
column_properties = collections.OrderedDict([
        ('Group',
         ColumnProperties(is_numeric=False, display_name='Group')),
        ('Item',
         ColumnProperties(is_numeric=False, display_name='Item')),
        ('Volume',
         ColumnProperties(is_numeric=True,  display_name='Volume')),
        ('Price',
         ColumnProperties(is_numeric=True,  display_name='Price')),
        ('HubVolume',
         ColumnProperties(is_numeric=True,  display_name='%s Volume' % MARKET_HUB)),
        ('HubPrice',
         ColumnProperties(is_numeric=True,  display_name='%s Price' % MARKET_HUB)),
        ('HubRelative',
         ColumnProperties(is_numeric=True,  display_name='Relative to %s' % MARKET_HUB)),
])

Item = namedtuple('Item', ['id', 'name', 'group', 'category', 'market_group_id'])
Row = namedtuple('Row', column_properties.keys())
MarketGroup = namedtuple('MarketGroup', ['id', 'parent_id', 'name', 'good_name'])

id2item = {}
name2item = {}
market_groups = {}
market_group_useful_names = {}

conn = None
try:
    conn = sqlite3.connect('eve-dump.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if len(cursor.fetchall()) == 0:
        raise Exception("database is empty")
except Exception as e:
    print("Could not open Eve database dump:", str(e), file=sys.stderr)
    sys.exit(1)

def get_system_id(name):
    c = conn.cursor()
    c.execute("SELECT itemID from invnames where itemName = ?", (name,))
    return c.fetchone()[0]

def load_items():
    c = conn.cursor()
    c.execute("select typeId, typeName, groupName, categoryName, marketGroupID from invtypes join invgroups on invtypes.groupID = invgroups.groupID join invcategories on invgroups.categoryID = invcategories.categoryID")

    for entry in c:
        item = Item(*entry)
#        print(item)
        name2item[item.name] = item
        id2item[item.id] = item


def load_marketgroups():
    c = conn.cursor()
    c.execute("select marketGroupID, parentGroupID, marketGroupName from invmarketgroups")

    for entry in c:
        market_group = MarketGroup(*entry, good_name=None)
        market_groups[market_group.id] = market_group

    for (id, mg) in market_groups.items():
        market_groups[id] = mg._replace(good_name = useful_market_group_name(id))

# Get the list of all parents of a market group
def get_parents(id):
    trace = []
    while id:
        mg = market_groups[id]
        trace.append(mg.name)
        id = mg.parent_id
    return trace

# This, using some ad-hoc rules, tries to produce a useful category
# name.  There is nothing particularly principled about this, but I
# like the results.
def useful_market_group_name(id):
    # Grab the list of all of the parent market groups
    parents = list(reversed(get_parents(id)))
    if len(parents) == 1:
        return parents[0]
    elif parents[0] == 'Ship Modifications':
        if len(parents) >= 3 and parents[1] == 'Rigs':
            # Rig groups are named like "Electronics Superiority Rigs".
            # We want "Rigs - Electronics Superiority"
            name_body = parents[2].rsplit(None, 1)[0]
            rig_name = 'Rigs - ' + name_body
            return rig_name
        else: # This is just Subsystems, I think
            return parents[1]
    elif parents[0] == 'Ship Equipment':
        # Everything under "Ship Equipment" is a module... except for deployables
        if parents[1] == 'Deployable Equipment':
            return parents[1]
        else:
            # "Electronics and Sensor Upgrades" is really long and should be shorter.
            name = parents[1] if parents[1] != "Electronics and Sensor Upgrades" else "Electronics Upgrades"
            return 'Modules - ' + name
    elif parents[0] == 'Ships':
        # Get the real name for t2 ship classes
        if len(parents) >= 4 and parents[2].startswith("Advanced"):
            return 'Ships - ' + parents[3]
        else:
            return 'Ships - ' + parents[1]
    else:
        return parents[0]


def download_data(ids, system_id):
    base_url = 'http://api.eve-central.com/api/marketstat?hours=%d&usesystem=%d&' % (EVECENTRAL_HOURS, system_id)
    suffix = "&".join("typeid=%d" % i for i in ids)
    url = base_url + suffix
#    print(url)
    s = urlreq.urlopen(url)
    s = "".join(x.decode() for x in s)
#    print(s)

    obj = parseString(s)
    return obj

def chunk(l, size):
    chunked = []
    i = 0
    while i < len(l):
        chunked.append(l[i:i+size])
        i += size
    return chunked

def read_xml_field(node, key):
    return node.getElementsByTagName(key)[0].childNodes[0].data

def handle_data(table, xml, hub_xml):
    items = xml.getElementsByTagName("type")
    hub_iter = iter(hub_xml.getElementsByTagName("type"))
    for item_report in items:
        i = int(item_report.getAttribute("id"))
        item = id2item[i]

        sell = item_report.getElementsByTagName("sell")[0]
        volume = int(read_xml_field(sell, "volume"))
        min_price = float(read_xml_field(sell, "min"))
        price_fmted = "{:,.2f}".format(min_price)

        hub_item = next(hub_iter)
        assert int(hub_item.getAttribute("id")) == i
        hub_sell = hub_item.getElementsByTagName("sell")[0]
        hub_volume = int(read_xml_field(hub_sell, "volume"))
        hub_min_price = float(read_xml_field(hub_sell, "min"))
        hub_price_fmted = "{:,.2f}".format(hub_min_price)

        hub_relative_formatted = "?"
        if volume > 0:
            hub_relative = (min_price - hub_min_price) * 100.0 / (hub_min_price)
            hub_relative_formatted = "{:.1f}%".format(hub_relative)

        row = Row(Item=item.name, Volume=volume, Price=price_fmted, HubVolume=hub_volume, HubPrice=hub_price_fmted, HubRelative=hub_relative_formatted, Group=market_groups[item.market_group_id].good_name)
        table.append(row)

def text_output(table, system):
    for parts in table:
        print(parts)

def make_tag(name, attribs=None):
    if attribs:
        return "<%s %s>" % (name, ' '.join("{!s}={!r}".format(key,val) for (key,val) in attribs.items()))
    else:
        return "<%s>" % name

def make_row(open_tag, close_tag, entries, classes=None):
    fmt_string = (open_tag + "%s" + close_tag) * len(entries)
    cells = fmt_string % tuple(entries)
    attribs = {}
    if classes:
        attribs['class'] = ' '.join(classes)
    return "%s%s%s" % (make_tag('tr', attribs), cells, '</tr>')

def format_table(table):
    table_output = ""
    for entry in table:
        classes = []
        if entry.Volume == 0:
            classes.append('market_hole')
        elif not entry.HubRelative.startswith("?"):
            if entry.HubRelative[0] == '-':
                classes.append('relative_negative')
            else:
                classes.append('relative_positive')

        table_output += make_row("<td>", "</td>", entry, classes=classes) + "\n"

    return table_output


def html_output(table, system):
    page_template = """
<html><head><title>%(system)s market data</title>
<!-- DataTables CSS -->
<link rel="stylesheet" type="text/css" href="http://ajax.aspnetcdn.com/ajax/jquery.dataTables/1.9.4/css/jquery.dataTables.css">
<link rel="stylesheet" type="text/css" href="market.css">

<!-- jQuery -->
<script type="text/javascript" charset="utf8" src="http://ajax.aspnetcdn.com/ajax/jQuery/jquery-1.8.2.min.js"></script>

<!-- DataTables -->
<script type="text/javascript" charset="utf8" src="http://ajax.aspnetcdn.com/ajax/jquery.dataTables/1.9.4/jquery.dataTables.min.js"></script>
<script type="text/javascript" charset="utf-8">

// formatted numbers sorting based on http://datatables.net/plug-ins/sorting#formatted_numbers
jQuery.extend( jQuery.fn.dataTableExt.oSort, {
    "formatted-num-pre": function ( a ) {
        if (a === "-" || a === "") {
            return 0;
        } else if (a === "?") {
          // Special case for unknowns
          return Number.POSITIVE_INFINITY;
        } else {
          // Replace characters that aren't digits, '-' or '.'.
          return parseFloat(a.replace(/[^\d\-\.]/g, ""));
       }
    },

    "formatted-num-asc": function ( a, b ) {
        return a - b;
    },

    "formatted-num-desc": function ( a, b ) {
        return b - a;
    }
} );

$(document).ready(function() {
  $('#market').dataTable( {
    "aoColumnDefs": [
      { "sType": "formatted-num", "aTargets": %(numeric_columns)s }
    ],
    "bPaginate": false,
    "bLengthChange": false,
  } );
} );
</script>

</head><body>
<p>Data may be out of date or missing. Items might be in the wrong station. Price shown is the lowest price. If you want more items on here, message sully on IRC with links to lists of items (probably pastebinned).
<p><strong>Want to help out and keep this up-to-date?
Run the <a href="/poller">poller</a> while ship-spinning in Catch!</strong>

<h1>%(system)s market</h1>
<em>Last updated %(timestamp)s [EVE time], from
<a href="http://eve-central.com">eve-central</a> data no more than %(data_age)d hours old
at that time.</em><br>

<table border=1 id='market'>
<thead>%(header)s</thead>
<tbody>
%(table)s
</tbody></table></body></html>"""

    f = open(system + ".html", "w")

    print(page_template % {
        'system': system,
        'numeric_columns': [index for index, column in enumerate(column_properties.values()) if column.is_numeric],
        'header': make_row("<th>", "</th>", [column.display_name for column in column_properties.values()]),
        'timestamp': email.utils.formatdate(usegmt=True),
        'data_age': EVECENTRAL_HOURS,
        'table': format_table(table),
        },
          file = f)
    f.close()

def make_table(formatter, system):
    item_names = [s.strip() for s in open(ITEM_LIST)]
#    print(item_names)
    item_ids = [name2item[name].id for name in item_names]
#    print(item_ids)

#    for name in item_names:
#        item = name2item[name]
#        print(item.name, "----", market_groups[item.market_group_id].good_name, "----", get_parents(item.market_group_id))

    system_id = get_system_id(system)
    hub_system_id = get_system_id(MARKET_HUB)
    table = []
    for part in chunk(item_ids, CHUNK_SIZE):
        data = download_data(part, system_id)
        hub_data = download_data(part, hub_system_id)
        handle_data(table, data, hub_data)

    formatter(table, system)

def make_tables(formatter, systems):
    for system in systems:
        make_table(formatter, system)

def make_poller():
    item_names = [s.strip() for s in open(ITEM_LIST)]
    item_ids = [name2item[name].id for name in item_names]
    print("item_ids = %s;" % str(item_ids), file=open("id_list.js", "w"))

def filter_input():
    for name in sys.stdin:
        name = name.strip()
        if name in name2item:
            print(name)

def main(args):
    load_items()
    load_marketgroups()

    if len(args) > 1 and args[1] == "--filter":
        filter_input()
    elif len(args) > 1 and args[1] == "--poller":
        make_poller()
    elif len(args) > 2 and args[1] == "--text":
        make_tables(text_output, args[2:])
    elif len(args) > 1:
        make_tables(html_output, args[1:])
        make_poller()
    else:
        sys.stderr.write("usage: %s --filter | --text SYSTEMS | SYSTEMS\n" % args[0])
        return 1

if __name__ == '__main__':
    sys.exit(main(sys.argv))
