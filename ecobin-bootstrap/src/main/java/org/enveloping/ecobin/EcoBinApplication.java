package org.enveloping.ecobin;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.security.autoconfigure.UserDetailsServiceAutoConfiguration;

@SpringBootApplication(exclude = UserDetailsServiceAutoConfiguration.class)
public class EcoBinApplication {

    public static void main(String[] args) {
        SpringApplication.run(EcoBinApplication.class, args);
    }

}
