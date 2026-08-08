# Geometry Attributes

Geometry attributes store per-element data. A point attribute stores one value
per point; a primitive attribute stores one value per primitive; a detail
attribute stores one value for the whole geometry.

## Attribute types

An attribute may be float, integer, vector or string. Vector attributes hold
three floats. The attribute type is fixed once the attribute is created.

## Attribute promotion

Attribute promotion moves an attribute between classes, for example promoting
a point attribute to a primitive attribute by averaging.
