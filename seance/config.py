""" Handles unifying command line arguments, environment variables, and config file settings. """

import os
import builtins
import argparse
import configparser
from typing import Optional, Any
from dataclasses import dataclass, field, asdict

@dataclass
class ConfigOption:
    """ Represents a configurable option. `short` should be specified without the leading dash.

    `name` should be the unambigous option name, which then becomes the key name in the INI file, verbatim
    (including spaces, since `configparser` lets you have keys with spaces). The command-line --option-name is then
    computed from that by converting to lowercase and replacing spaces with `-` characters. Likewise, the
    environment variable name is computed by converting to uppercase and replacing spaces with underscores.
    These can each be overriden with `env_name` and `cmdline_name` init keyword arguments.

    Note: `ConfigHandler` prepends the value of the `env_var_prefix` keyword argument to its `__init__()`
    to the name specified here, when matching environment variables.

    Note: the value of `required` is *not* passed to argparse, in case the option was specified
    via a config file or an environment variable. `ConfigHandler` will read the value of `required`
    to ensure that this configuration option is set *somewhere*, but it isn't required to be set in
    any particular place.
    """

    name: str
    short: Optional[str] = None
    cmdline_name: Optional[str] = None
    _argparse_name: str = field(init=False)
    env_name: Optional[str] = None

    required: bool = False
    metavar: Optional[str] = None
    type: builtins.type = str

    # It says Any but technically it should be the same type as `type`.
    default: Any = None
    help: Optional[str] = None

    # Extra keyword arguments to pass to argparse's `add_argument()` method.
    argparse_add_kwargs: dict = field(default_factory=dict)


    def __post_init__(self):

        if self.cmdline_name is None:
            self.cmdline_name = self.name.lower().replace(' ', '-')

        if self.env_name is None:
            self.env_name = self.name.upper().replace(' ', '_')


        self._argparse_name = self.cmdline_name.replace('-', '_')


class ConfigHandler:
    """ Registers configurable options, and handles parsing them from command line arguments, a config file, and
    environment variables.

    Note: `env_var_prefix` is prepended literally. An underscore is *not* automatically inserted between the
    specified prefix and the option's name.
    """


    @staticmethod
    def _configparser_optionxform(key_name):
        return key_name.lower().replace(' ', '_').replace('-', '_')


    def _set_value_for(self, option: ConfigOption, value):

        self.option_values_by_name[option.name] = value
        self.option_values_by_env_name[option.env_name] = value
        self.option_values_by_argparse_name[option._argparse_name] = value


    def __init__(self, options: list[ConfigOption], *, env_var_prefix='', config_section,
            argparse_init_kwargs=None, argparser_class=argparse.ArgumentParser,
            configparser_init_kwargs=None, configparser_class=configparser.ConfigParser,
        ):


        # Ensure no options have duplicate names.
        option_names = [option.name.lower() for option in options]
        for name in option_names:
            if option_names.count(name) > 1:
                raise ValueError(f"conflicting options with name '{name}' found")

        self.options = options
        self.env_var_prefix = env_var_prefix
        self.config_section = config_section
        argparse_init_kwargs = {} if argparse_init_kwargs is None else argparse_init_kwargs
        self.argparser = argparser_class(**argparse_init_kwargs)
        configparser_init_kwargs = {} if configparser_init_kwargs is None else configparser_init_kwargs
        self.configparser = configparser_class(interpolation=None, **configparser_init_kwargs)
        self.configparser.optionxform = self._configparser_optionxform

        # Dictionary of option names to their values.
        self.option_values_by_name = {}
        self.option_values_by_env_name = {}
        self.option_values_by_argparse_name = {}

        # Add a --config argument.
        self.argparser.add_argument('--config', required=False, metavar='PATH',
            help="The config file to read from, if any."
        )

        for option in options:

            # Add the parts that aren't None.
            add_argument_args = []
            if option.short is not None:
                add_argument_args.append(f'-{option.short}')

            add_argument_args.append(f'--{option.cmdline_name}')

            add_argument_kwargs = option.argparse_add_kwargs.copy()

            option_dict = asdict(option)

            # And again add the parts that aren't None.
            for kw in ['metavar', 'help']:
                if option_dict[kw] is not None:
                    add_argument_kwargs[kw] = option_dict[kw]

            # Bools are special cased.
            if option.type == bool:
                add_argument_kwargs['action'] = argparse.BooleanOptionalAction
            elif option.type is not None:
                add_argument_kwargs['type'] = option.type


            arg = self.argparser.add_argument(
                *add_argument_args,
                required=False, # Semanitcally required options aren't required to be specified as cmdline args.
                **add_argument_kwargs,
            );


    def parse_all_sources(self) -> argparse.Namespace:

        # Priority: command-line arguments > environment variables > config file.

        cmdline_args = self.argparser.parse_args()

        config_file = cmdline_args.config
        if config_file:

            files_read = self.configparser.read(config_file)

            if not files_read:
                self.argparser.error('specified configuration file is invalid or does not exist')

            # Find the specified config section, but case-insensitively.
            match_section_ignorecase = lambda sect : sect.lower() == self.config_section.lower()
            sections = self.configparser.sections()
            section_name = next(filter(match_section_ignorecase, sections))
            section = self.configparser[section_name]


        for option in self.options:

            option_init = option.type

            cmdline_val = getattr(cmdline_args, option._argparse_name, option.default)
            if cmdline_val is not None:
                self._set_value_for(option, option_init(cmdline_val))
                continue

            env_name = f'{self.env_var_prefix}{option.env_name}'
            env_val = os.getenv(env_name)
            if env_val is not None:
                self._set_value_for(option, option_init(env_val))
                continue

            if config_file:
                config_val = section.get(option.name)
                if config_val is not None:

                    # Config files handle bools specially.
                    if option.type == bool:
                        self._set_value_for(option, section.getboolean(option.name))
                    else:
                        self._set_value_for(option, option_init(config_val))

                    continue

            # If we've reached this point, then the option hasn't been specified anywhere.
            # Set its value to the specified default value.
            self._set_value_for(option, option.default)


        # Now that we've gathered every option from every source, it's time to
        # check the `required` value for each option.

        missing_option_names = []
        for option in self.options:
            if option.required:
                # None is equivalent to "not supplied".
                if self.option_values_by_argparse_name[option._argparse_name] is None:
                    missing_option_names.append(option.name)

        if missing_option_names:
            self.argparser.error('the following options are required: {}'.format(', '.join(missing_option_names)))

        namespace = argparse.Namespace(**self.option_values_by_argparse_name)
        namespace.argparser = self.argparser

        return namespace
