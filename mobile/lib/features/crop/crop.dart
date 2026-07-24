/// A crop the farmer can pick before a call — one row of the Growz catalogue.
///
/// [id] is the real Growz crop UUID (the same id the Growz app uses, so it is
/// ready for a future chat integration); [name] is the Uzbek display name.
library;

class Crop {
  const Crop({
    required this.id,
    required this.name,
    this.biologyName = '',
    this.category = '',
  });

  final String id;
  final String name;
  final String biologyName;
  final String category;

  factory Crop.fromJson(Map<String, dynamic> json) => Crop(
    id: (json['id'] ?? '') as String,
    name: (json['name'] ?? '') as String,
    biologyName: (json['biology_name'] ?? '') as String,
    category: (json['category'] ?? '') as String,
  );
}
