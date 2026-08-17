# Playlist Synchronization

SongMirror keeps one logical playlist aligned across music services while making authority and conflict boundaries explicit.

## Language

**Authoritative group**:
Two or more selected music services whose confirmed membership changes define the logical playlist together.
_Avoid_: Two-way mode, partial N-way

**Order authority**:
The member of an authoritative group whose playlist names and track sequence define the canonical ordering.
_Avoid_: Primary provider, master

**Mirror**:
A selected music service that receives the authoritative membership and order but never contributes changes back to them.
_Avoid_: Peer, secondary authority

**Authority baseline**:
The last trusted membership snapshot for each member of an authoritative group, used to distinguish additions and removals from pre-existing differences.
_Avoid_: Initial union, sync history
