# Attribute Transfer

The attribute transfer node copies attributes from a source geometry onto a
destination geometry. Attribute transfer matches points by proximity and
blends the source attribute values into the destination attribute.

## Attribute transfer parameters

The distance threshold controls how far the attribute transfer searches for
source points. Attributes beyond the threshold are not transferred. The blend
width softens the attribute falloff.

## Transferring multiple attributes

List attribute names separated by spaces to transfer several attributes in one
attribute transfer node.
