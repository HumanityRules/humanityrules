import com.github.jengelman.gradle.plugins.shadow.tasks.ShadowJar

plugins {
    id("java")
    id("com.gradleup.shadow") version "8.3.3"
}

group = "com.dip"
version = "1.0-SNAPSHOT"

repositories {
    mavenCentral()
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(21))
    }
}


dependencies {
    testImplementation(platform("org.junit:junit-bom:5.10.0"))
    testImplementation("org.junit.jupiter:junit-jupiter")

    // AWS Lambda
    implementation("com.amazonaws:aws-lambda-java-core:1.2.2")
    implementation("com.amazonaws:aws-lambda-java-events:3.11.1")
    runtimeOnly("com.amazonaws:aws-lambda-java-log4j2:1.5.1")
}

// ==== Shadow Jar Config : Fix the jar name
tasks.withType<ShadowJar> {
    archiveFileName.set("lambda-uberjar.jar")
}

tasks.test {
    useJUnitPlatform()
}