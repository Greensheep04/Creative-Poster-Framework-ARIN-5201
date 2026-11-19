You are the Creative Director in a multi-agent poster system.

Create one coherent brief for a 720 × 1280 portrait poster.

Keywords: -----

Constraints:
----

Return only valid JSON with these fields:
- concept: string
- headline: string
- supporting_copy: string
- keyword_mapping: array of objects containing keyword and representation
- visual_direction: string
- style: object containing mood, medium, and palette (array)
- must_include: array of strings
- must_avoid: array of strings

Include exactly one mapping for each original keyword.
Each keyword must have a concrete visual or textual expression.
All elements must support the same central concept.


```
Keywords: ocean, plastic, hope

Constraints:
- Optimistic tone.
- English headline: at most 7 words.
- Supporting copy: at most 18 words.
- One main visual metaphor.
- Do not invent statistics, brands, or organizations.
- Leave exact positions and font sizes to the Layout Planner.
```

- Qwen3-4B-Instruct-2507
``` json
{  
"concept": "A wave gently lifting a plastic bottle into the sky, transforming it into a bird that soars above the ocean, symbolizing hope through mindful change.",  
"headline": "Hope Grows from the Tide",  
"supporting_copy": "Plastic meets the ocean — transformed into flight, not waste.",  
"keyword_mapping": [  
{  
"keyword": "ocean",  
"representation": "Vast, serene blue horizon with gentle ripples and distant sun rays"  
},  
{  
"keyword": "plastic",  
"representation": "A single, intact plastic bottle rising from the water, mid-transition"  
},  
{  
"keyword": "hope",  
"representation": "The bottle morphs into a flying bird made of clear and translucent plastic"  
}  
],  
"visual_direction": "Soft focus on the ocean, with a single dynamic upward motion forming the central metaphor",  
"style": {  
"mood": "optimistic and serene",  
"medium": "watercolor with subtle digital layering",  
"palette": ["sky blue", "soft turquoise", "white", "transparent pastel orange"]  
},  
"must_include": [  
"ocean",  
"plastic",  
"hope"  
],  
"must_avoid": [  
"statistics",  
"brands",  
"organizations",  
"dark tones",  
"chaos",  
"destruction",  
"fear"  
]  
}
```


- Qwen3-1.7B
``` json
{  
"concept": "Hope in the Ocean",  
"headline": "Hope in the Ocean",  
"supporting_copy": "Every piece of plastic removed brings us closer to a cleaner future.",  
"keyword_mapping": [  
{  
"keyword": "ocean",  
"representation": "A shimmering, wave-filled canvas with a sun rising over it"  
},  
{  
"keyword": "plastic",  
"representation": "A swirling vortex of translucent plastic bottles"  
},  
{  
"keyword": "hope",  
"representation": "A glowing, heart-shaped light illuminating the ocean"  
}  
],  
"visual_direction": "A dynamic composition where the ocean's surface is transformed from a polluted state to a hopeful one, with plastic bottles swirling into a radiant, sunlit horizon.",  
"style": {  
"mood": "optimistic",  
"medium": "digital",  
"palette": ["deep blue", "emerald green", "golden light"]  
},  
"must_include": ["ocean", "plastic", "hope"],  
"must_avoid": ["statistics", "brands", "organizations"]  
}

```
- Qwen3.5-4B
```json
{  
"concept": "Transformative restoration of marine life through digital waste reshaping.",  
"headline": "Renew the ocean, restore the hope.",  
"supporting_copy": "Small debris turns to stars, lighting our shared path to clear seas.",  
"keyword_mapping": [  
{  
"keyword": "ocean",  
"representation": "Deep blue water background"  
},  
{  
"keyword": "plastic",  
"representation": "Fragmented debris morphing into coral"  
},  
{  
"keyword": "hope",  
"representation": "Golden light rising from the center"  
}  
],  
"visual_direction": "A single plastic bottle morphs into a blooming sunken coral reef emitting light.",  
"style": {  
"mood": ["Optimistic", "Bright", "Clean"],  
"medium": ["Digital Illustration"],  
"palette": ["#006994", "#FF7F50", "#FFD700", "#FFFFFF"]  
},  
"must_include": ["natural imagery", "light effects"],  
"must_avoid": ["grainy textures", "realistic trash", "dark shadows"]  
}
```




```
Keywords: tradition, technology, harmony

Constraints:
- English headline, at most 6 words.
- No supporting copy.
- Exactly two palette colors.
- No split-screen composition.
- One main visual metaphor.
```
- Qwen3-4B-Instruct-2507
```json
{  
"concept": "An ancient clay pot fused with a digital interface, its surface glowing with flowing data streams that ripple like water, symbolizing harmony between tradition and technology.",  
"headline": "Clay Meets Code, Together",  
"supporting_copy": "",  
"keyword_mapping": [  
{  
"keyword": "tradition",  
"representation": "Hand-crafted clay pot with organic, hand-shaped details"  
},  
{  
"keyword": "technology",  
"representation": "Glowing, flowing data streams emanating from the pot’s surface"  
},  
{  
"keyword": "harmony",  
"representation": "The data streams gently follow the pot's curves, merging without conflict"  
}  
],  
"visual_direction": "Symmetrical focus on a central object where tradition and technology seamlessly blend",  
"style": {  
"mood": "calm and balanced",  
"medium": "digital illustration with hand-drawn texture",  
"palette": ["terracotta", "soft silver"]  
},  
"must_include": [  
"tradition",  
"technology",  
"harmony"  
],  
"must_avoid": [  
"split-screen",  
"conflict",  
"machinery",  
"chaos",  
"statistics",  
"brands",  
"organizations"  
]  
}
```

- Qwen3-1.7B
```json
{  
"concept": "Harmony in Tradition and Technology",  
"headline": "Tradition, Technology, and Harmony",  
"keyword_mapping": [  
{  
"keyword": "tradition",  
"representation": "A weathered wooden arch with aged carvings"  
},  
{  
"keyword": "technology",  
"representation": "A sleek, glowing circuit board in a modern structure"  
},  
{  
"keyword": "harmony",  
"representation": "A flowing ribbon of light connecting the two elements"  
}  
],  
"visual_direction": "A central bridge with a traditional wooden arch on one side and a modern tech structure on the other, connected by a glowing light ribbon symbolizing harmony.",  
"style": {  
"mood": "serene",  
"medium": "digital",  
"palette": ["deep blue", "warm amber"]  
},  
"must_include": ["tradition", "technology", "harmony"],  
"must_avoid": ["supporting copy", "split-screen", "statistics", "brands", "organizations"]  
}
```
- Qwen3.5-4B
```json
{  
"concept": "Integrating heritage and innovation in a single visual form.",  
"headline": "Heritage Meets Innovation",  
"supporting_copy": "",  
"keyword_mapping": [  
{"keyword": "tradition", "representation": "Intricate textile pattern woven in charcoal ink."},  
{"keyword": "technology", "representation": "Digital circuit lines rendered in neon blue."},  
{"keyword": "harmony", "representation": "Smooth gradient blending where lines dissolve into the background."}  
],  
"visual_direction": "Portrait composition featuring a singular abstract vehicle shaped like a traditional scroll winding into a modern fiber-optic strand, unified without borders.",  
"style": {  
"mood": "Serene and Futuristic",  
"medium": "Digital Vector Illustration",  
"palette": [  
"Charcoal Ink",  
"Neon Blue"  
]  
},  
"must_include": [  
"Max 6 word headline",  
"Exactly two palette colors",  
"One main visual metaphor",  
"No split-screen"  
],  
"must_avoid": [  
"Copy for headline only",  
"Split composition",  
"Foreign text",  
"More than two colors"  
]  
}
```