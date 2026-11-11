#version 140

// Inputs
in vec3 position;
in vec3 normal;
in mat4 inst_m;

// Output data
out VS_OUT {
    vec3 position;
    vec3 normal;
    mat4 mvp;
} vs_out;

// Uniform data
uniform mat4 M;
uniform mat4 V;
uniform mat4 P;

// Render loop
void main() {
    vs_out.mvp = P * V * M * inst_m;
    vs_out.position = position;
    vs_out.normal = normal;

    gl_Position = vec4(position, 1.0);
}
