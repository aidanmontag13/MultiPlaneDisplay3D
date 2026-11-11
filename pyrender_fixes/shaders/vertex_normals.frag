#version 140

out vec4 frag_color;

uniform vec4 normal_color;

void main()
{
    frag_color = normal_color;
}
